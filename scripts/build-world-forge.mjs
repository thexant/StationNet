import { build } from 'esbuild';

// World Forge: the MUD world editor at /mud/forge. Source in stationnet/world_forge/ui;
// the bundle (world-forge.js plus the world-forge.css it imports) is committed like the office apps.
await build({
  entryPoints: ['stationnet/world_forge/ui/main.tsx'],
  outfile: 'stationnet/static/world-forge.js',
  bundle: true,
  minify: true,
  format: 'iife',
  platform: 'browser',
  target: ['chrome111', 'firefox113', 'safari16.2'],
  jsx: 'automatic',
  legalComments: 'external',
  define: { 'process.env.NODE_ENV': '"production"' },
});
