"use strict";

// A complete StationNet application: plain JavaScript plus the Visual API.
StationNet.start((ui) => {
  let target;
  let attempts;

  function newGame() {
    target = Math.floor(Math.random() * 100) + 1;
    attempts = 0;
    showGame("The station has selected a number from 1 to 100.");
  }

  function showGame(message, tone) {
    ui.render(
      ui.title("Number Guess"),
      ui.text(message, { tone }),
      ui.numberInput("guess", "Your guess", { min: 1, max: 100, step: 1 }),
      ui.button("Transmit Guess", submitGuess)
    );
  }

  function submitGuess(event) {
    const guess = Number(event.values.guess);
    if (!Number.isInteger(guess) || guess < 1 || guess > 100) {
      showGame("Enter a whole number between 1 and 100.", "warning");
      return;
    }
    attempts += 1;
    if (guess < target) showGame(`${guess} is too low. Attempts: ${attempts}`, "accent");
    else if (guess > target) showGame(`${guess} is too high. Attempts: ${attempts}`, "accent");
    else {
      ui.render(
        ui.title("Signal Matched"),
        ui.text(`Correct! The number was ${target}. You found it in ${attempts} attempt${attempts === 1 ? "" : "s"}.`, { emphasis: true }),
        ui.button("Play Again", newGame)
      );
    }
  }

  newGame();
});
