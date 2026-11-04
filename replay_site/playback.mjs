// Pure timing functions shared by the player and Node tests.
export function scrubTime(value, duration) {
  const t = Number(value);
  return Number.isFinite(t) ? Math.min(Math.max(0, t), Math.max(0, duration)) : 0;
}
export function frameIndex(frames, t) {
  if (!frames.length) return -1;
  let low = 0, high = frames.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (frames[mid].t <= t) low = mid + 1;
    else high = mid;
  }
  return Math.max(0, low - 1);
}
export function nearestEvent(events, t) {
  if (!events.length) return -1;
  const i = frameIndex(events, t);
  return i + 1 < events.length && Math.abs(events[i+1].t-t) < Math.abs(events[i].t-t) ? i+1 : i;
}
export function latestEvent(events, t, matches) {
  for (let i = frameIndex(events, t); i >= 0; i--) {
    if (events[i].t <= t && matches(events[i])) return events[i];
  }
  return null;
}
export function advanceTime(t, elapsedSeconds, speed, duration) {
  return scrubTime(t + Math.max(0, elapsedSeconds) * speed, duration);
}
