// 新信卡片的大頭像(the maintainer 10-14「彈窗要放大皇冠、⚓、👔、🧞 這種角色 icon 當成圖像」):
// 用 macOS 的 Apple Color Emoji 把一個 emoji 畫成 96×96,再縮成 W×H 個像素(透明度加權平均),印 JSON {w,h,px:[[r,g,b,a],...]}。
// 用法:osascript -l JavaScript emoji-raster.js <emoji> <W> <H>
ObjC.import('AppKit')
function run(argv) {
  const emoji = argv[0], W = parseInt(argv[1] || '12', 10), H = parseInt(argv[2] || '12', 10), S = 96
  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(null, S, S, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0)
  const ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep)
  $.NSGraphicsContext.saveGraphicsState
  $.NSGraphicsContext.setCurrentContext(ctx)
  const attrs = $.NSDictionary.dictionaryWithObjectForKey($.NSFont.fontWithNameSize('Apple Color Emoji', S * 0.78), $.NSFontAttributeName)
  const str = $(emoji), sz = str.sizeWithAttributes(attrs)
  str.drawAtPointWithAttributes($.NSMakePoint((S - sz.width) / 2, (S - sz.height) / 2), attrs)
  ctx.flushGraphics
  $.NSGraphicsContext.restoreGraphicsState
  const px = [], bx = S / W, by = S / H
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let r = 0, g = 0, b = 0, a = 0, n = 0
    for (let yy = Math.floor(y * by); yy < Math.floor((y + 1) * by); yy += 2) for (let xx = Math.floor(x * bx); xx < Math.floor((x + 1) * bx); xx += 2) {
      const c = rep.colorAtXY(xx, yy), al = c.alphaComponent
      r += c.redComponent * al; g += c.greenComponent * al; b += c.blueComponent * al; a += al; n++ }
    px.push(a > 0 ? [Math.round(r / a * 255), Math.round(g / a * 255), Math.round(b / a * 255), Math.round(a / n * 255)] : [0, 0, 0, 0])
  }
  return JSON.stringify({ w: W, h: H, px })
}
