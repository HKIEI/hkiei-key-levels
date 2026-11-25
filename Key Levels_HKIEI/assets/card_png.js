// Rasterize the generated local SVG through macOS AppKit when sips cannot decode it.
ObjC.import('AppKit');

function run(paths) {
    const image = $.NSImage.alloc.initWithContentsOfFile(paths[0]);
    if (!image || image.isNil()) throw Error('未能讀取報告卡');
    const bitmap = $.NSBitmapImageRep.imageRepWithData(image.TIFFRepresentation);
    if (!bitmap || bitmap.isNil()) throw Error('未能繪製報告卡');
    const png = bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
    if (!png || png.isNil() || !png.writeToFileAtomically(paths[1], true)) throw Error('未能寫入 PNG');
}
