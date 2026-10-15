// aib boards 小窗格:面板的 .ts 彼此 import 不寫副檔名(./charts)——Node 找不到時補上 .ts 再找一次(設計稿 §23.3)
export async function resolve(spec, ctx, next) {
  try { return await next(spec, ctx) } catch (e) {
    if (e?.code === 'ERR_MODULE_NOT_FOUND' && /^\.\.?\//.test(spec) && !/\.[cm]?[jt]s$/.test(spec)) return next(spec + '.ts', ctx)
    throw e
  }
}
