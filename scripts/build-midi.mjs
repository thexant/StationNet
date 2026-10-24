import { build } from "esbuild";
import { copyFileSync } from "node:fs";

await build({
  entryPoints: ["stationnet/static/midi/engine-source.js"],
  outfile: "stationnet/static/midi/engine.js",
  bundle: true,
  format: "esm",
  minify: true,
  legalComments: "inline",
  target: "es2022",
});
copyFileSync("node_modules/spessasynth_lib/dist/spessasynth_processor.min.js",
             "stationnet/static/midi/spessasynth_processor.min.js");
