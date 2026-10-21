"use strict";

// A complete StationNet application: plain JavaScript plus the Visual API.
StationNet.start(async (ui) => {
  let target;
  let attempts = 0;
  let elapsed = 0;
  let best = await ui.storage.get("best-score").catch(() => null);

  function newGame() {
    target = Math.floor(Math.random() * 100) + 1;
    attempts = 0;
    elapsed = 0;
    ui.render(
      ui.title("Number Guess"),
      ui.text("A number from 1 to 100 has been selected.", { id: "message" }),
      ui.text(scoreLine(), { id: "score", tone: "muted" }),
      ui.text("TIME: 0s", { id: "timer", tone: "accent" }),
      ui.numberInput("guess", "Your guess", { id: "guess", min: 1, max: 100, step: 1, onSubmit: submitGuess }),
      ui.button("Submit Guess", submitGuess)
    );
  }

  function scoreLine() {
    return `ATTEMPTS: ${attempts} / PERSONAL BEST: ${best == null ? "--" : best}`;
  }

  async function submitGuess(event) {
    const guess = Number(event.values.guess);
    if (!Number.isInteger(guess) || guess < 1 || guess > 100) {
      ui.update("message", ui.text("Enter a whole number between 1 and 100.", { id: "message", tone: "warning" }));
      return;
    }
    attempts += 1;
    ui.update("score", ui.text(scoreLine(), { id: "score", tone: "muted" }));
    if (guess < target) ui.update("message", ui.text(`${guess} is too low.`, { id: "message", tone: "accent" }));
    else if (guess > target) ui.update("message", ui.text(`${guess} is too high.`, { id: "message", tone: "accent" }));
    else {
      if (best == null || attempts < best) {
        best = attempts;
        await ui.storage.set("best-score", best).catch(() => {});
      }
      const sharedOptions = { scope: "shared" };
      const wins = (await ui.storage.get("community-wins", sharedOptions).catch(() => 0)) || 0;
      await ui.storage.set("community-wins", wins + 1, sharedOptions).catch(() => {});
      target = null;
      ui.render(
        ui.title("Number Found"),
        ui.text(`Correct! The number was ${target}. You found it in ${attempts} attempt${attempts === 1 ? "" : "s"}.`, { emphasis: true }),
        ui.table(["Personal best", "Community wins"], [[best, wins + 1]]),
        ui.button("Play Again", newGame)
      );
    }
  }

  ui.interval(() => {
    if (!target || attempts < 0) return;
    elapsed += 1;
    ui.update("timer", ui.text(`TIME: ${elapsed}s`, { id: "timer", tone: "accent" }));
  }, 1000);
  newGame();
});
