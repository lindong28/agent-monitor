export function createDeviceConnection({request, controller, current = () => true, deviceFeedback = () => {}, deviceConnect, deviceCancel, onConnected = () => {}}) {
  let pairing = null;
  controller.signal.addEventListener("abort", () => pairing?.finish(new DOMException("页面已离开", "AbortError")), {once:true});
  deviceCancel?.addEventListener("click", () => pairing?.finish(new Error("已取消本机连接；本次操作未继续。")));
  function requireDevice() {
    if (pairing) return pairing.promise;
    if (controller.signal.aborted) return Promise.reject(new DOMException("页面已离开", "AbortError"));
    // Keep window.open inside this user gesture, before any await/import/fetch.
    // Re-pair on each action so a refresh or a restarted local service needs no
    // separate connect click, and no capability is persisted in browser storage.
    const nonce = [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, "0")).join("");
    const popup = window.open(`http://127.0.0.1:39002/pair?origin=${encodeURIComponent(location.origin)}&nonce=${nonce}`, "agent-monitor-device", "width=520,height=240");
    if (!popup) {
      const message = "浏览器阻止了本机连接窗口。请允许此页面打开弹窗，再重试原操作；本次操作未继续。";
      deviceFeedback(message, true);
      return Promise.reject(new Error(message));
    }
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const attempt = { promise, validating: false, finished: false, timer: null, poll: null, receive: null };
    pairing = attempt;
    attempt.finish = (error, connection, result) => {
      if (attempt.finished) return;
      attempt.finished = true;
      clearTimeout(attempt.timer);
      clearInterval(attempt.poll);
      window.removeEventListener("message", attempt.receive);
      window.removeEventListener("pagehide", attempt.leave);
      popup.close();
      if (pairing === attempt) pairing = null;
      if (deviceCancel) deviceCancel.hidden = true;
      if (deviceConnect) deviceConnect.disabled = false;
      if (error) {
        deviceFeedback(error.message, error.name !== "AbortError");
        reject(error);
      } else {
        deviceFeedback(`已连接当前设备：${result.label}。断开时停止操作，不会切回服务器出口。`);
        onConnected();
        resolve(connection);
      }
    };
    attempt.receive = async event => {
      if (attempt.finished || attempt.validating || event.origin !== "http://127.0.0.1:39002" || event.source !== popup
          || event.data?.type !== "agent-monitor-network" || event.data?.nonce !== nonce) return;
      attempt.validating = true;
      clearInterval(attempt.poll);
      popup.close();
      if (event.data.error) {
        attempt.finish(new Error(`本机连接端未能完成配对：${event.data.error} 请按下方连接帮助恢复后重试。`));
        return;
      }
      deviceFeedback("已收到本机响应，正在确认它与服务器的连接…");
      try {
        const connection = event.data.connection;
        if (!connection || typeof connection.id !== "string" || typeof connection.capability !== "string") throw new Error("本机返回的配对信息不完整。");
        const result = await request("network-status", { connection });
        if (!result.connected) throw new Error("服务器尚未确认本机连接可用。");
        if (!current()) { attempt.finish(new DOMException("页面已离开", "AbortError")); return; }
        attempt.finish(null, connection, result);
      } catch (error) {
        attempt.finish(error.name === "AbortError" ? error : new Error(`本机与服务器的连接校验未通过：${error.message} 请在本机执行 agent-monitor connect status，恢复连接后重试原操作。`));
      }
    };
    attempt.leave = () => attempt.finish(new DOMException("页面已离开", "AbortError"));
    window.addEventListener("pagehide", attempt.leave);
    window.addEventListener("message", attempt.receive);
    attempt.timer = setTimeout(() => attempt.finish(new Error(attempt.validating
      ? "本机与服务器的连接校验超时，本次操作未继续。请执行 agent-monitor connect status，恢复后重试原操作。"
      : "未收到本机连接端的配对响应，本次操作未继续。请在当前设备按下方帮助启用或检查连接服务，再重试原操作。")), 20000);
    attempt.poll = setInterval(() => {
      if (popup.closed && !attempt.validating) attempt.finish(new Error("本机连接窗口已关闭，配对未完成；本次操作未继续。可重试原操作，或展开连接帮助检查服务。"));
    }, 250);
    if (deviceConnect) deviceConnect.disabled = true;
    if (deviceCancel) deviceCancel.hidden = false;
    deviceFeedback("正在连接当前设备的网络…首次使用需启用本机连接服务；完成连接后将继续当前操作。");
    return promise;
  }
  return requireDevice;
}
