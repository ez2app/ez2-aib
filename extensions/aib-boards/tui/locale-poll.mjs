// aib boards 小窗格的語系輪詢(純邏輯,main.mjs 用;測試直接載入這支,不起整個畫面)。
// 每輪:設定檔 mtime 沒變就什麼都不做(不起 Python);變了 ⇒ resolve() 問語系 ⇒ 語系換了才 load() 讀字典 ⇒ onSwitch()。
// 「看過了」(seen)只在整輪成功後才記(審查 10-14:原本先記才問,問失敗或字典讀失敗就要等檔案再變一次才重試);
// 還不知道要看哪些檔(開窗時 --resolve 失敗)⇒ 每輪都重問。
// 連續問失敗(例如這台沒有 python3):前 3 次每輪都問,之後每 2、4、8、12 輪才問一次(5 秒一輪 ⇒ 最慢一分鐘一次),問到就歸零。
//   deps = { mtimes(files) → {檔: mtimeMs}, resolve(cb(err, {loc, files})), load(loc, cb(err, data)), current() → 現在語系, onSwitch(loc, data) }
export function makePoller(deps, files = [], seen = {}) {
  let busy = false, fails = 0, wait = 0
  const st = { files, seen }
  function tick() {
    if (busy) return false
    const m = deps.mtimes(st.files)
    if (st.files.length && !Object.keys(m).some(f => m[f] !== st.seen[f]) && Object.keys(st.seen).length === Object.keys(m).length) return false
    if (wait > 0) { wait--; return false }                                // 連續失敗後退讓
    busy = true
    deps.resolve((err, r) => {
      if (err || !r || !r.loc) { busy = false; fails++; if (fails >= 3) wait = Math.min(12, 2 ** (fails - 2)) - 1; return }   // 問不到:seen 不動 ⇒ 之後再問
      fails = 0
      st.files = r.files ?? []
      const fresh = deps.mtimes(st.files)
      if (r.loc === deps.current()) { st.seen = fresh; busy = false; return }
      deps.load(r.loc, (err2, data) => {
        busy = false
        if (err2 || !data) return                                         // 字典讀不到:seen 不動 ⇒ 下一輪再試
        st.seen = fresh
        deps.onSwitch(r.loc, data)
      })
    })
    return true
  }
  return { tick, state: st }
}
