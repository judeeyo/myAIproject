"use strict";

const $ = (id) => document.getElementById(id);
const form = $("jobForm");
const STORE_KEY = "ktx-ui-settings-v1";
const SAVED_FIELDS = ["loginId", "dep", "arr", "date", "from", "to", "trains", "intervalMin", "intervalMax"];

const STATUS_TEXT = {
  idle: "대기 중", starting: "준비 중", login: "로그인 중", polling: "조회 중",
  reserving: "예약 시도", success: "완료", stopped: "중지됨", error: "오류",
  checking: "조회 테스트", checked: "조회 성공",
};
const MODE_TEXT = { reserve: "예약까지", pay: "결제까지", dryrun: "테스트", demo: "데모" };
const MODE_HINT = {
  reserve: "좌석을 잡으면 알림을 보냅니다. 결제는 10분 안에 코레일톡 앱에서 직접 하세요.",
  pay: "좌석을 잡으면 카드 결제를 한 번 시도합니다.",
  dryrun: "빈자리를 찾으면 예약하지 않고 멈춥니다. 동작 확인용입니다.",
  demo: "코레일에 접속하지 않고 모의 데이터로 화면 흐름을 연습합니다.",
};

let lastLogId = 0;
let lastToastId = null;
let running = false;
let pollTimer = null;

/* ---------- 세그먼트 · 스테퍼 ---------- */

function segValue(name) {
  const group = form.querySelector(`.segmented[data-name="${name}"]`);
  const checked = group.querySelector('[aria-checked="true"]');
  return checked ? checked.dataset.value : null;
}

function setSeg(name, value) {
  const group = form.querySelector(`.segmented[data-name="${name}"]`);
  if (!group) return;
  group.querySelectorAll(".seg").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === value)));
}

form.querySelectorAll(".segmented").forEach((group) => {
  group.addEventListener("click", (e) => {
    const btn = e.target.closest(".seg");
    if (!btn || running) return;
    setSeg(group.dataset.name, btn.dataset.value);
    refresh();
  });
});

function stepValue(name) {
  return Number(form.querySelector(`.stepper[data-name="${name}"]`).dataset.value);
}

function setStep(name, value) {
  const el = form.querySelector(`.stepper[data-name="${name}"]`);
  const v = Math.max(Number(el.dataset.min), Math.min(Number(el.dataset.max), value));
  el.dataset.value = String(v);
  el.querySelector("output").textContent = String(v);
}

form.querySelectorAll(".stepper").forEach((el) => {
  el.addEventListener("click", (e) => {
    if (running) return;
    const name = el.dataset.name;
    if (e.target.closest(".inc")) setStep(name, stepValue(name) + 1);
    if (e.target.closest(".dec")) setStep(name, stepValue(name) - 1);
    refresh();
  });
});

$("swapBtn").addEventListener("click", () => {
  const dep = $("dep").value;
  $("dep").value = $("arr").value;
  $("arr").value = dep;
  refresh();
});

form.addEventListener("input", refresh);

/* ---------- 화면 갱신 ---------- */

function totalPassengers() {
  return stepValue("adult") + stepValue("child") + stepValue("senior");
}

function refresh() {
  const mode = segValue("mode");
  $("paymentBox").hidden = mode !== "pay";
  $("modeHint").textContent = MODE_HINT[mode];
  $("adjacentRow").hidden = totalPassengers() < 2;

  $("sumRoute").textContent = `${$("dep").value || "출발"} → ${$("arr").value || "도착"}`;
  const parts = [];
  if (stepValue("adult")) parts.push(`어른 ${stepValue("adult")}`);
  if (stepValue("child")) parts.push(`어린이 ${stepValue("child")}`);
  if (stepValue("senior")) parts.push(`경로 ${stepValue("senior")}`);
  $("sumPsg").textContent = parts.join(" · ") || "인원 없음";
  const date = $("date").value ? $("date").value.slice(5).replace("-", "/") + " " : "";
  $("sumMode").textContent = `${date}${$("from").value}~${$("to").value} · ${MODE_TEXT[mode]}`;
  saveSettings();
}

function setLocked(lock) {
  running = lock;
  form.querySelectorAll("input, .seg, .stepper button, #swapBtn").forEach((el) => { el.disabled = lock; });
  $("startBtn").disabled = lock;
  $("checkBtn").disabled = lock;
  $("stopBtn").disabled = !lock;
}

function showErrors(message) {
  $("errors").textContent = message || "";
  $("errors").hidden = !message;
}

/* ---------- 설정 기억 (비밀번호·카드는 저장하지 않음) ---------- */

function saveSettings() {
  try {
    const data = { mode: segValue("mode"), train: segValue("train"), seat: segValue("seat"), adjacent: segValue("adjacent") };
    SAVED_FIELDS.forEach((k) => { data[k] = $(k).value; });
    ["adult", "child", "senior"].forEach((k) => { data[k] = stepValue(k); });
    localStorage.setItem(STORE_KEY, JSON.stringify(data));
  } catch (_) { /* 저장 불가 환경은 무시 */ }
}

function loadSettings() {
  let data = null;
  try { data = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (_) { data = null; }
  if (data) {
    SAVED_FIELDS.forEach((k) => { if (typeof data[k] === "string") $(k).value = data[k]; });
    ["adult", "child", "senior"].forEach((k) => { if (Number.isFinite(data[k])) setStep(k, data[k]); });
    ["mode", "train", "seat", "adjacent"].forEach((k) => { if (data[k]) setSeg(k, data[k]); });
    if (data.mode === "pay") setSeg("mode", "reserve"); // 결제 모드는 매번 직접 선택
  }
  const today = new Date();
  const iso = new Date(today.getTime() - today.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  if (!$("date").value || $("date").value < iso) $("date").value = iso;
  $("date").min = iso;
}

/* ---------- 서버 통신 ---------- */

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

function payload(mode) {
  const data = {
    mode,
    loginId: $("loginId").value,
    loginPassword: $("loginPassword").value,
    dep: $("dep").value, arr: $("arr").value, date: $("date").value,
    from: $("from").value, to: $("to").value,
    adult: stepValue("adult"), child: stepValue("child"), senior: stepValue("senior"),
    train: segValue("train"), seat: segValue("seat"),
    adjacent: totalPassengers() > 1 && segValue("adjacent") === "adjacent",
    trains: $("trains").value, intervalMin: $("intervalMin").value, intervalMax: $("intervalMax").value,
  };
  if (mode === "pay") {
    data.card = {
      number: $("cardNumber").value, password: $("cardPassword").value,
      expire: $("cardExpire").value, birth: $("cardBirth").value,
    };
  }
  return data;
}

async function start(mode) {
  showErrors("");
  if (mode === "pay" && !confirm("좌석을 잡으면 카드 결제까지 자동으로 진행합니다. 시작할까요?")) return;
  try {
    await api("/api/start", payload(mode));
    $("loginPassword").value = "";
    ["cardNumber", "cardPassword", "cardExpire", "cardBirth"].forEach((k) => { $(k).value = ""; });
    setLocked(true);
    poll();
  } catch (err) {
    showErrors(err.message);
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  start(segValue("mode"));
});
$("checkBtn").addEventListener("click", () => start("check"));
$("stopBtn").addEventListener("click", () => api("/api/stop", {}).catch((err) => showErrors(err.message)));
$("clearLog").addEventListener("click", () => {
  api("/api/clear", {}).then(() => { $("log").innerHTML = ""; renderEmptyLog(); }).catch(() => {});
});

/* ---------- 상태 표시 ---------- */

function renderEmptyLog() {
  if ($("log").children.length) return;
  const li = document.createElement("li");
  li.className = "empty";
  li.textContent = "> 설정을 마치고 [예매 시작]을 누르세요";
  $("log").appendChild(li);
}

function appendLogs(logs) {
  const box = $("log");
  const empty = box.querySelector(".empty");
  if (logs.length && empty) empty.remove();
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  logs.forEach((entry) => {
    const li = document.createElement("li");
    li.className = entry.level;
    const time = document.createElement("time");
    time.textContent = entry.time;
    const msg = document.createElement("span");
    msg.textContent = entry.msg;
    li.append(time, msg);
    box.appendChild(li);
    lastLogId = Math.max(lastLogId, entry.id);
  });
  while (box.children.length > 400) box.firstChild.remove();
  if (logs.length && nearBottom) box.scrollTop = box.scrollHeight;
}

function renderTrains(trains) {
  const box = $("trainChips");
  box.replaceChildren();
  (trains || []).forEach((t) => {
    const chip = document.createElement("span");
    const has = t.general || t.special;
    chip.className = `train-chip ${has ? "has" : "none"}`;
    const dot = document.createElement("i");
    const seats = [t.general ? "일반" : "", t.special ? "특실" : ""].filter(Boolean).join("/") || "매진";
    chip.append(dot, `${t.time} ${t.no} ${seats}`);
    chip.title = `${t.type} ${t.no}번 ${t.time} · ${seats}`;
    box.appendChild(chip);
  });
}

function render(data) {
  const s = data.state;
  const status = s.status || "idle";
  $("statusPill").dataset.state = status;
  $("hero").dataset.state = status;
  $("statusText").textContent = STATUS_TEXT[status] || status;
  $("statMessage").textContent = s.message || STATUS_TEXT[status];
  $("statAttempts").textContent = String(s.attempts || 0);
  $("statLastCheck").textContent = s.lastCheck || "--:--:--";
  renderTrains(s.trains);
  appendLogs(data.logs || []);

  if (s.toast && s.toast.id !== lastToastId) {
    const first = lastToastId === null;
    lastToastId = s.toast.id;
    if (!first) showToast(s.toast.title, s.toast.body);
  }
  if (running !== data.running) setLocked(data.running);
}

function showToast(title, body) {
  const kind = /실패|확인 필요|이용제한/.test(title) ? "error" : "";
  const el = $("toast");
  el.className = `toast ${kind}`;
  $("toastTitle").textContent = title;
  $("toastBody").textContent = body;
  el.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { el.hidden = true; }, 8000);
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body, icon: "icon.svg" });
    }
  } catch (_) { /* 알림 미지원 */ }
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    render(await api(`/api/state?after=${lastLogId}`));
  } catch (_) {
    $("statusText").textContent = "서버 연결 끊김";
    $("statusPill").dataset.state = "error";
  }
  pollTimer = setTimeout(poll, running ? 700 : 2000);
}

/* ---------- PC 알림 ---------- */

function renderNotifyBtn() {
  const btn = $("notifyBtn");
  if (!("Notification" in window)) { btn.hidden = true; return; }
  const on = Notification.permission === "granted";
  btn.textContent = on ? "PC 알림 켜짐" : Notification.permission === "denied" ? "알림 차단됨" : "PC 알림 켜기";
  btn.disabled = on || Notification.permission === "denied";
}
$("notifyBtn").addEventListener("click", async () => {
  try { await Notification.requestPermission(); } catch (_) { /* 무시 */ }
  renderNotifyBtn();
});

/* ---------- 시작 ---------- */

(async function init() {
  loadSettings();
  renderNotifyBtn();
  renderEmptyLog();
  try {
    const config = await api("/api/config");
    const list = $("stationList");
    config.stations.forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      list.appendChild(opt);
    });
    $("envLoginHint").hidden = !config.envLogin;
  } catch (_) { /* 서버가 늦게 뜨면 poll 이 다시 시도 */ }
  refresh();
  poll();
})();
