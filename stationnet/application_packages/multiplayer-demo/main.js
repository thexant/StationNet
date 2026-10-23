StationNet.start(async (ui) => {
  let session = null;
  let messages = [];

  const draw = (status = "Choose an action") => ui.render(
    ui.title("Signal Pairs"),
    ui.text(status, { id: "status", tone: "accent" }),
    ui.input("Session ID", { id: "session-id" }),
    ui.button("CREATE TWO-PLAYER SESSION", create),
    ui.button("JOIN SESSION", join, { variant: "secondary" }),
    ui.button("SEND PING", ping, { variant: "secondary" }),
    ui.list(messages.slice(-8), { id: "messages" })
  );

  async function create() {
    const result = await ui.multiplayer.create({ name: "Signal Pairs", maxPlayers: 2, state: { pings: 0 } });
    session = result.session.id;
    draw(`Created ${session}. Sign in as another user in a second browser and join this ID.`);
  }

  async function join({ values }) {
    const id = String(values["session-id"] || "").trim();
    const result = await ui.multiplayer.join(id);
    session = result.session.id;
    draw(`Joined ${session} as ${result.player_id}.`);
  }

  async function ping() {
    if (!session) throw new Error("Create or join a session first");
    await ui.multiplayer.send("ping", { sent_at: Date.now() });
  }

  ui.multiplayer.onEvent((event) => {
    const sender = event.player ? event.player.username : "SERVER";
    messages.push(`${sender}: ${event.event}`);
    ui.update("messages", ui.list(messages.slice(-8), { id: "messages" }));
  });
  draw();
});
