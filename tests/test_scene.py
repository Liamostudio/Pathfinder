"""SamScene and the runtime hook: label on change not on a timer, log every call, and never move if the table cannot be seen."""
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from lookandpick.adapters.fake import FakeTracker, SimArm  # noqa: E402
from lookandpick.adapters.sam_client import SamWorkerError  # noqa: E402
from lookandpick.models.base import ChatResult, FakeLLM, ModelError  # noqa: E402
from lookandpick.models.calllog import CallLog  # noqa: E402
from lookandpick.runtime import Runtime  # noqa: E402
from lookandpick.scene import SamScene, SceneUnavailable, frame_changed  # noqa: E402
from lookandpick.scene_labeller import SceneLabelError  # noqa: E402
from lookandpick.world import TrackedObject  # noqa: E402


def frame(grey=40, patch=None):
    f = np.full((72, 128, 3), grey, np.uint8)
    if patch:
        f[10:60, 10:100] = patch
    return f


class Labeller:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def label(self, f):
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Tracker:
    def __init__(self):
        self.refreshes, self.fail = [], False

    def refresh(self, f, prompts):
        if self.fail:
            raise SamWorkerError("worker down")
        self.refreshes.append(list(prompts))
        return self.objects()

    def objects(self):
        return [TrackedObject(f"{p}-1", p, (0.1, 0.1, 0.0)) for p in (self.refreshes[-1] if self.refreshes else [])]


def test_frame_change_detection():
    assert frame_changed(None, frame())
    assert not frame_changed(frame(), frame())
    assert not frame_changed(frame(), frame(grey=42))                      # sensor noise
    assert frame_changed(frame(), frame(patch=200))                        # something big appeared


def test_spark_is_asked_only_when_the_picture_changes_but_sam_runs_every_time():
    labeller, tracker, current = Labeller([["red cube"], ["red cube", "blue mug"]]), Tracker(), [frame()]
    scene = SamScene(lambda: current[0], labeller, tracker)
    scene.refresh()
    scene.refresh()
    scene.refresh()
    assert labeller.calls == 1 and len(tracker.refreshes) == 3 and scene.labelled == 1       # same picture: one Spark call, three SAM runs
    current[0] = frame(patch=220)
    scene.refresh()
    assert labeller.calls == 2 and tracker.refreshes[-1] == ["red cube", "blue mug"]


def test_fixed_prompts_never_call_spark():
    labeller, tracker = Labeller([["x"]]), Tracker()
    SamScene(lambda: frame(), labeller, tracker, fixed_prompts=["red cube"]).refresh()
    assert labeller.calls == 0 and tracker.refreshes == [["red cube"]]


def test_every_sam_call_is_in_the_timeline_with_what_was_found():
    log = CallLog()
    SamScene(lambda: frame(), Labeller([["red cube", "blue mug"]]), Tracker(), log).refresh()
    entry = [e for e in log.entries() if e["source"] == "SAM 3.1"][0]
    assert entry["input"] == "red cube, blue mug" and "red cube: 1 found" in entry["output"] and entry["status"] == "ok"
    assert entry["latency_ms"] >= 0


def test_a_missing_frame_or_a_dead_worker_means_the_scene_is_unavailable():
    log = CallLog()
    with pytest.raises(SceneUnavailable, match="no camera frame"):
        SamScene(lambda: None, Labeller([["x"]]), Tracker(), log).refresh()
    tracker = Tracker()
    scene = SamScene(lambda: frame(), Labeller([["red cube"]]), tracker, log)
    tracker.fail = True
    with pytest.raises(SceneUnavailable, match="SAM worker"):
        scene.refresh()
    assert [e["status"] for e in log.entries() if e["source"] == "SAM 3.1"] == ["error"]


def test_a_failed_relabel_keeps_the_old_names_but_a_first_failure_is_fatal():
    log, current = CallLog(), [frame()]
    scene = SamScene(lambda: current[0], Labeller([["red cube"], ModelError("spark down")]), Tracker(), log)
    scene.refresh()                                                        # labelled once
    current[0] = frame(patch=220)
    scene.refresh()                                                        # relabel fails: keep ["red cube"], do not go blind
    assert scene.labels == ["red cube"] and any(e["source"] == "Scene labeller" and e["status"] == "error" for e in log.entries())
    with pytest.raises(SceneUnavailable, match="could not name"):
        SamScene(lambda: frame(), Labeller([SceneLabelError("bad json")]), Tracker(), CallLog()).refresh()


def test_an_empty_table_is_a_scene_not_an_error():
    tracker = Tracker()
    assert SamScene(lambda: frame(), Labeller([[]]), tracker).refresh() == [] and tracker.refreshes == [[]]


# ---- the runtime hook -------------------------------------------------------------------------------------------------
def runtime(scene, model=None):
    model = model or FakeLLM([ChatResult("Done.")])
    return Runtime(model, arm=SimArm(), tracker=FakeTracker(), log=CallLog(), say=lambda _: None, scene=scene), model


def test_the_runtime_looks_at_the_table_before_each_command():
    looked = []

    class Scene:
        def refresh(self):
            looked.append(1)
    rt, model = runtime(Scene())
    result = rt.command("what can you see?")
    assert looked == [1] and result.reply == "Done." and len(model.requests) == 1


def test_if_the_table_cannot_be_seen_the_model_is_not_called_and_nothing_moves():
    class Blind:
        def refresh(self):
            raise SceneUnavailable("worker down")
    rt, model = runtime(Blind())
    result = rt.command("pick up the red cube")
    assert model.requests == [] and result.steps == [] and "won't move" in result.reply and "scene unavailable" in result.error
    assert rt.said and "can't see the table" in rt.said[-1]


def test_a_stop_word_never_waits_for_the_scene():
    class Slow:
        def refresh(self):
            raise AssertionError("a stop must not look at the table first")
    rt, model = runtime(Slow())
    assert rt.command("STOP").reply == "stopped" and model.requests == []
