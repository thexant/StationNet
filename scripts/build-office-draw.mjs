import { build } from 'esbuild';

await build({
  entryPoints: ['stationnet/office_draw/main.js'],
  outfile: 'stationnet/static/office-draw.js',
  bundle: true,
  minify: true,
  format: 'iife',
  platform: 'browser',
  target: ['chrome100', 'firefox100', 'safari16'],
  legalComments: 'external',
  define: { 'process.env.NODE_ENV': '"production"' },
});
