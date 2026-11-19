const form = document.getElementById('event-form');
const notice = document.getElementById('event-notice');

async function post(url, payload) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || '交接事件处理失败');
  return data;
}

form.onsubmit = async (event) => {
  event.preventDefault();
  const runId = document.getElementById('run-id').value;
  const shipId = document.getElementById('ship-id').value.trim();
  const payload = {
    event_id: document.getElementById('event-id').value.trim(),
    action: document.getElementById('action').value,
    at: document.getElementById('at').value.trim(),
  };
  notice.textContent = '正在施加交接事件…';
  try {
    const reply = await post(
      `/api/8/runs/${runId}/handovers/${encodeURIComponent(shipId)}/events`,
      payload);
    const shifts = reply.record.shifts
      .map((s) => `${s.pilot}:${s.state}v${s.revision}`).join(' / ');
    notice.textContent =
      `${reply.applied ? '已生效' : '幂等重放（未重复占班）'} · 状态 ${reply.record.status} · ${shifts}`;
  } catch (error) {
    notice.textContent = error.message;
  }
};
