// node --import ./reg.mjs main.mjs:登記上面那個補副檔名的掛鉤
import { register } from 'node:module'
register('./ts-ext.mjs', import.meta.url)
