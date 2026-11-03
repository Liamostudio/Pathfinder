"""SceneLabeller: ask Muse Spark (vision) what is on the table, in words SAM 3.1 can find.

One model call per scene change, never on a timer, and every call is in the call log by name with its latency and cost. The model sees
the frame and returns a short list of noun phrases; this module validates them hard, because they become SAM prompts. People, hands
and the arm are dropped: those belong to the hand gate, not to targets.
"""
from __future__ import annotations

import base64
import json
import re

from .models.base import ChatModel

PROMPT = ("This is a camera view of a robot's table. List the separate small objects lying on the table that a robot arm could pick up, "
          "as short noun phrases a segmentation model can find, with colour and kind (for example: red cube, blue mug). "
          "Ignore people, hands, the robot arm, the table and the printed marker. "
          'Answer with JSON only, like {"objects": ["red cube", "blue mug"]}. If there are none, answer {"objects": []}.')
BANNED = ("hand", "finger", "person", "people", "human", "arm", "robot", "gripper", "table", "marker", "desk", "background")
_PHRASE = re.compile(r"^[a-z][a-z0-9 \-]{1,38}$")


class SceneLabelError(ValueError):
    """The model's answer could not be turned into a safe list of prompts."""


def parse_labels(text: str, max_objects: int = 6) -> list[str]:
    if not text.strip():
        raise SceneLabelError("the model ran out of tokens before it answered")        # Spark reasons first; too small a budget leaves no answer
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise SceneLabelError("the model did not answer with JSON")
    try:
        items = json.loads(text[start:end + 1]).get("objects")
    except (json.JSONDecodeError, AttributeError):
        raise SceneLabelError("the model's JSON is malformed") from None
    if not isinstance(items, list):
        raise SceneLabelError("the model's JSON has no 'objects' list")
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        phrase = " ".join(item.lower().split())
        if _PHRASE.match(phrase) and not any(word in phrase.split() or word in phrase for word in BANNED) and phrase not in out:
            out.append(phrase)
    return out[:max_objects]


class SceneLabeller:
    def __init__(self, model: ChatModel, max_objects: int = 6, max_tokens: int = 2500):
        # Spark reasons before it answers (about 700 tokens for this task, measured); 600 left it with an empty reply.
        self.model, self.max_objects, self.max_tokens = model, max_objects, max_tokens

    def label(self, frame) -> list[str]:
        import cv2
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise SceneLabelError("cannot encode the frame")
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg.tobytes()).decode()
        reply = self.model.chat([{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                                              {"type": "image_url", "image_url": {"url": url}}]}], max_tokens=self.max_tokens)
        return parse_labels(reply.content, self.max_objects)
