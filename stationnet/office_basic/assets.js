// QBJS resources embedded in the runner bundle. The runner has an opaque origin
// and no network access, so fonts, GX font sheets, and the standard QBJS
// libraries are preloaded from here instead of being fetched.
import dosvga from '../static/vendor/qbjs/qbjs.woff2';
import dosvga8 from '../static/vendor/qbjs/fonts/WebPlus_IBM_EGA_8x8.woff';
import gxFont from '../static/vendor/qbjs/gx/__gx_font_default.png';
import gxFontBlack from '../static/vendor/qbjs/gx/__gx_font_default_black.png';
import qb64pe from '../static/vendor/qbjs/lib/compatibility/qb64pe.bi';
import graphics2d from '../static/vendor/qbjs/lib/graphics/2d.bas';
import ioFs from '../static/vendor/qbjs/lib/io/fs.bas';
import langArray from '../static/vendor/qbjs/lib/lang/array.bas';
import langObject from '../static/vendor/qbjs/lib/lang/object.bas';
import langString from '../static/vendor/qbjs/lib/lang/string.bas';
import langSystem from '../static/vendor/qbjs/lib/lang/system.bas';
import unitTest from '../static/vendor/qbjs/lib/test/unit-test.bas';
import webConsole from '../static/vendor/qbjs/lib/web/console.bas';
import webDom from '../static/vendor/qbjs/lib/web/dom.bas';
import webStorage from '../static/vendor/qbjs/lib/web/storage.bas';

export const fonts = { dosvga, dosvga8 };

export const binaryFiles = {
  'gx/__gx_font_default.png': gxFont,
  'gx/__gx_font_default_black.png': gxFontBlack,
};

export const textFiles = {
  'lib/compatibility/qb64pe.bi': qb64pe,
  'lib/graphics/2d.bas': graphics2d,
  'lib/io/fs.bas': ioFs,
  'lib/lang/array.bas': langArray,
  'lib/lang/object.bas': langObject,
  'lib/lang/string.bas': langString,
  'lib/lang/system.bas': langSystem,
  'lib/test/unit-test.bas': unitTest,
  'lib/web/console.bas': webConsole,
  'lib/web/dom.bas': webDom,
  'lib/web/storage.bas': webStorage,
};
