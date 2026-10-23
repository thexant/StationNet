"use strict";

StationNet.start(async (ui) => {
  const { greeting } = await ui.modules.load("src/greeting.js");
  const config = ui.assets.json("assets/config.json");
  const note = ui.assets.text("assets/note.txt");
  ui.render(ui.title(greeting(config.name)), ui.text(note));
});
