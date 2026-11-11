---
title: StationNet
subtitle: Official User's Guide and Operator's Reference
edition: First Edition
version: For StationNet Version 1.0
date: November 2026
---

<!-- frontmatter: notice -->

## Notice

This manual describes StationNet Version 1.0 as distributed in November 2026.
StationNet is under continuing development, and later releases may differ in
detail from the descriptions given here.

StationNet is modular. Each station is set up by its own operator, who decides
which services are offered, who may use them, and what material they contain.
The services, channels, books, games, programs, and worlds you find on a
particular station may therefore differ from the examples in this manual.

Screen names, button captions, and commands are printed exactly as they appear
on the screen. Where the station operator may change a setting, the manual says
so.

Product names mentioned in this manual are used for identification only and
may be trademarks of their respective owners.

## Preface

StationNet is a self-hosted communications and information service. A single
computer on a home, school, club, or office network runs the station, and
everyone else reaches it with an ordinary web browser. No outside service,
subscription, or Internet connection is required once a station is installed.

This manual is written first for the people who use a station: those who read
the bulletins, exchange mail, listen to the radio, read in the library, work in
the Office programs, or play in the MUD. Parts I through V assume no knowledge
of StationNet beyond basic familiarity with a computer and a web browser.

Parts VI and VII serve two smaller audiences. Part VI is for the station
operator, called the SYSOP, who installs StationNet, starts and stops stations,
and administers accounts and content. Part VII is a short orientation for
developers who wish to write programs, themes, or game worlds for StationNet.

### How This Manual Is Organized

**Part I, Getting Started**, explains what a station is, how to connect,
how to create an account, and how the screen is arranged. It ends with a short
guided session.

**Part II, Communication and Community**, covers the user directory, chat,
private mail, the bulletin board, the imageboard, events, and the file archive.

**Part III, Information and Entertainment**, covers radio, television, videos,
the library, the reference library (WIKI), programs, the arcade, and the MUD.

**Part IV, Office**, covers the six Office programs: Documents, Sheets,
Presentations, Draw, Agenda, and BASIC.

**Part V, Personalization and Everyday Use**, collects the settings and habits
that apply to every service: appearance, sound, notifications, installation on
a phone or desktop, station hours, and account security.

**Part VI, Station Hosting and Administration**, is for SYSOPs.

**Part VII, Developer's Reference**, is for programmers.

The **Appendices** contain a keyboard reference, a quick reference to common
operations, a troubleshooting guide, a table of modules and permissions, and a
glossary.

### Conventions Used in This Manual

`MONOSPACED CAPITALS` show text that appears on the screen, such as a tab
name or button caption: for example, choose `COMPOSE +`.

`Monospaced lower case` shows something you type exactly, such as a MUD
command (`/look`) or a file name (`station.json`).

Words in angle brackets stand for a value you supply. In `/go <direction>`,
replace `<direction>` with an actual direction, such as `north`.

**Choose** means click with the mouse or tap on a touch screen. **Press**
refers to a key on the keyboard.

Three kinds of notices appear in the text:

::: note
A NOTE gives additional information or a helpful suggestion.
:::

::: caution
A CAUTION warns of an action that may cause inconvenience or loss of work.
:::

::: warning
A WARNING identifies an action that permanently destroys information or
affects other people's access to the station.
:::

<!-- toc -->

# Part I. Getting Started

## 1. Introducing StationNet

### 1.1 What a Station Is

A *station* is one running copy of StationNet together with everything it
keeps: its name, its member accounts, its messages, and its files. The station
runs on a single host computer, which may be a desktop computer, a laptop, a
Raspberry Pi, or an Android phone. Other people reach the station over the
local network with a web browser.

Every station has a name chosen by its operator and a *callsign*, a short code
assigned when the station is first set up. Every member also receives a
callsign. Callsigns appear beside usernames throughout the system, in the form
`username - [CALLSIGN]`, so that members can be identified reliably.

One host computer may run several stations at once, each with its own name,
network port, accounts, and content. The stations do not share information with
one another.

### 1.2 Services and Modules

The facilities a station offers are called *services*. Each service is
provided by a *module* that the SYSOP may turn on or off. Bulletin board,
announcements, chat, mail, radio, files, the user directory, and programs are
normally on. The imageboard, events, WIKI, videos, library, arcade, television,
Office, and MUD are off until the SYSOP turns them on.

A service that is turned off does not appear in the navigation bar and cannot
be opened. If this manual describes a service you cannot find, the station you
are using does not offer it.

The SYSOP also supplies most of a station's content: the radio programming, the
television channels, the books and videos, the games, and the MUD worlds.
StationNet provides the means of presenting this material; it does not supply
the material itself.

### 1.3 Classes of User

StationNet distinguishes three classes of user.

**Guests** are visitors who have not signed in. If the station permits guest
access, a guest may read public services such as the bulletin board and
announcements, but may not post, chat, or send mail.

**Members** are visitors who have signed in to an account. The status bar at
the bottom of the screen shows a member as `MEMBER`.

**SYSOPs** are station operators. The person who sets up a station becomes its
SYSOP and is shown as `SYSOP`. A SYSOP has two additional tabs, `SYSOP CONTROL`
and `MODERATION`, and additional controls throughout the system. A SYSOP may
also delegate particular duties to members, such as hosting radio broadcasts,
creating events, moderating the imageboard, or building MUD worlds.

Parts I through V describe what a member can do. Operations reserved for SYSOPs
or delegated members are identified as such.

### 1.4 What You Need

To use a station you need:

- A computer, tablet, or phone on the same network as the station.
- A current web browser, such as Chrome, Edge, Firefox, or Safari.
- The station's address, which the SYSOP will give you.
- An account on the station, unless you intend only to browse as a guest.

Some services require more. Live voice on the radio requires a microphone and,
when you connect from a computer other than the host, a secure (`https://`)
address. Installing the station as an application on a phone or desktop also
requires a secure address. These requirements are described where they apply.

## 2. Connecting and Signing In

### 2.1 Opening the Station

Every station has an address of the form

```
http://<host-address>:<port>
```

For example, `http://192.168.1.20:8080`. The SYSOP will tell you the
correct address. If you are sitting at the host computer itself, the address is
usually `http://127.0.0.1:8080`.

Type the address into the address bar of your browser and press Enter. The
station's welcome page appears, showing the station's name in large characters,
its access policy, and, if the SYSOP has written one, a message about the
station.

::: note
Bookmark the station's address, or install it as an application (see
Chapter 30), so that you do not need to type it each time.
:::

### 2.2 Registration Policies

A strip at the top of the sign-in page reports the station's registration
policy:

`PUBLIC ENROLLMENT OPEN`
: Anyone may create an account.

`PRIVATE / INVITATION ONLY`
: A single-use invitation code from the SYSOP is required.

`ENROLLMENT CLOSED`
: New accounts cannot be created. Ask the SYSOP for assistance.

### 2.3 Creating an Account

To create an account:

1. On the sign-in page, choose `NO ACCOUNT? CREATE ONE ->`, or on a private
   station `HAVE AN INVITATION? ENROLL ->`.
2. On a private station, enter the invitation code in the **Invitation code**
   field. If you followed an invitation link, the code is already filled in.
3. Enter a **Username** of 3 to 24 characters. Letters, numbers, the
   underscore (`_`), and the hyphen (`-`) are allowed.
4. Enter a **Password** of at least 10 characters, and enter it again in
   **Confirm password**.
5. Choose `CREATE ACCOUNT`.

The station creates the account, assigns you a random callsign, signs you in,
and displays the confirmation `Account linked: <username> - [<callsign>]`.
Usernames cannot be changed later, so choose carefully.

### 2.4 Signing In

1. Choose `LOGIN` or `> AUTHENTICATE`.
2. Enter your **Username** and **Password**.
3. If this is your own device, you may select **Remember me on this device**.
4. Choose `LOGIN`.

When you are signed in, the station displays `Link established. Welcome,
<username>` and opens the **login sequence** (Section 2.6), or the STATION page
if the station has turned the login sequence off. If you were sent to the sign-in
page from another page, you return to that page instead.

With **Remember me on this device** selected, the station keeps you signed in
on that browser across restarts of the browser and of the station until you
disconnect. Without it, you are signed out when the browser is closed.

::: caution
Do not select **Remember me on this device** on a computer that other people
use.
:::

### 2.5 If You Forget Your Password

StationNet does not use e-mail and cannot send you a reset link. Instead:

1. Attempt to sign in. After a failed attempt, a `FORGOT PASSWORD` button
   appears.
2. Make sure your username is entered, then choose `FORGOT PASSWORD`.
3. The station generates a temporary password and sends it privately to the
   `ALERTS` of the station's SYSOPs. Contact a SYSOP, who will give it to you.
4. Sign in with the temporary password, then change it at once (Section 4.4).

### 2.6 The Login Sequence

After you sign in, many stations greet you with a short login sequence in the
manner of an old bulletin board system. The SYSOP chooses which panels appear
and in what order:

- **Station banner**: the station's ASCII banner.
- **Waiting for you**: what has arrived for you, such as `3 NEW MAIL`,
  `12 NEW BOARD MSGS` since your last call, unread alerts and direct messages,
  events you have answered starting within a day, and today's Dungeon of the
  Day if you still have attempts left. Each line links to the service.
- **Quote of the day**: one quote, the same for everyone all day.
- **Last callers**: the most recent calls, with your own previous call. A call
  is a sign-in, or opening the login sequence from the front page while
  remembered on this device.
- **Oneliners**: a wall of short messages. Type up to 80 characters and choose
  `POST`; you may post one oneliner a minute and remove your own with `[X]`.
- **Coming up**: events in the next two days.
- **Dungeon of the Day**: today's dungeon and your remaining attempts.

If you are remembered on this device, choosing `> ENTER THE STATION <` on the
station's front page also opens the login sequence.

Choose `> CONTINUE TO THE STATION <`, or press Enter, to open the STATION page.
The **Waiting for you** summary also appears on the station's front page while
you are signed in.

### 2.7 Disconnecting

To sign out, open the `STATION` tab and choose `[ DISCONNECT ]` in the
**CURRENT OPERATOR** panel. The station displays `Connection closed.`
Disconnecting also ends any direct chats you have open and turns off your radio.

## 3. The Station Workspace

### 3.1 The Screen

Every station page has the same general arrangement.

**The masthead.** The station's name, drawn in large text characters, appears
at the top. A short line of text, the *tagline*, may appear beneath it. The SYSOP
sets both.

**The station line.** Beneath the masthead, a line shows `YOUR STATION`, the
`SYSTEM DATE/TIME`, and several switches: `SFX`, which turns interface sounds on
or off; `VFX`, which turns a screen scan-line effect on or off; and, where the
station has a theme tune, `MUSIC`. These switches are described in Chapter 28.
If the station keeps regular hours, a second line shows them (Chapter 31).

**The navigation bar.** A row of tab names, separated by asterisks, leads to
each service: `STATION`, `BULLETIN BOARD`, `ANNOUNCEMENTS`, `CHAT`, `MAIL`,
`RADIO`, `FILES`, `USERS`, `PROGRAMS`, and others the station offers. The tab
for the page you are on is highlighted. `CHAT` and `MAIL` show a count when
something new is waiting.

**The work area.** The service you have chosen occupies the middle of the
screen. Messages from the station appear above it, prefixed `SYS://`.

**The radio player.** When the radio is available, a small player is docked at
the edge of every page. See Chapter 13.

**The status bar.** The bottom line shows the station callsign, your username
and callsign, and your class (`MEMBER` or `SYSOP`). It also contains the link
`GET YOUR OWN STATION`, which leads to a page of StationNet installation files
if the SYSOP has provided them.

### 3.2 The STATION Page

The `STATION` tab opens the **Service Directory**. On the left is a numbered
list of the station's services, each marked `* ONLINE`. Choose any entry to
open that service.

On the right, the **CURRENT OPERATOR** panel shows your username and class and
offers `[ PROFILE ]` and `[ DISCONNECT ]`. Beneath it, the **STATION ACTIVITY**
panel summarizes recent events: the newest bulletin and reply, the latest chat
message, the newest file and member, your unread mail, radio broadcasts now on
the air, and current television listings. Each item is a link to the service
concerned.

### 3.3 Conventions Common to All Services

StationNet services share a small number of conventions. Once learned, they
apply everywhere.

**Buttons** are shown in capitals. A button ending in `+`, such as
`NEW BULLETIN +`, creates something new.

**Item menus.** Items you are allowed to change, such as your own posts, carry
a small downward triangle (▾). Choose it to reveal actions such as `DELETE`.
StationNet asks you to confirm before anything is removed.

**Reporting.** Items written by other people carry a small `▾` actions menu.
Open it and choose `REPORT`, then type a reason of 10 to 500 characters when
asked to send that item to the SYSOPs for review. You may have one open report on any item and
may file up to ten reports a day.

**Times** are displayed in your own device's time zone.

**Keyboard navigation.** When you are not typing in a field, pressing `/`
moves the keyboard focus to the navigation bar (or, in the MUD, to the command
line). Press Esc to release it. Individual services add their own keys; these
are listed in Appendix A.

**Guests.** A guest who chooses a link that requires an account is sent to the
sign-in page and, after signing in, is returned to the page requested.

## 4. Your Profile and Account

### 4.1 Viewing Your Profile

Your *profile* is the page other members see when they look you up. To open
it, choose `[ PROFILE ]` on the STATION page, or choose your name in the user
directory.

A profile shows whether the member is online, the time last active, the
callsign, a short **Status** line, birthday and gender if supplied, the date the
member joined, and an **About** section.

### 4.2 Editing Your Profile

1. Open your profile and choose `[ EDIT PROFILE ]`. (The `USERS` tab also has
   an `EDIT MY PROFILE` button.)
2. Complete any of the following fields. All are optional.
   - **Profile photo**: a PNG, JPEG, GIF, or WebP image. It is reduced to fit
     within 128 by 128 pixels and replaces any earlier photo.
   - **Status**: a line of up to 120 characters shown under your name.
   - **About me**: up to 2,000 characters.
   - **Birthday**: `MM/DD`, or `YYYY-MM-DD` to include the year.
   - **Gender**: up to 40 characters.
3. Choose `UPDATE MY PROFILE`.

### 4.3 Theme Preferences

If the SYSOP permits personal themes, the profile editing page also contains
**Theme preferences**, where you may choose your own colours. See Section 28.1.

### 4.4 Changing Your Password

1. Open your profile and choose `[ EDIT PROFILE ]`.
2. Under **Change password**, enter your **Existing password**.
3. Enter a **New password** of at least 10 characters and confirm it.
4. Choose `CHANGE PASSWORD`.

Your StationNet password is separate from the Office password described in
Chapter 21.

### 4.5 Deleting Your Account

The **Delete account** section at the foot of the profile editing page
permanently removes your account and everything you have contributed: files,
videos, images, Office documents, bulletins, feed posts and comments, chat
messages, mail, events, radio uploads, saved program data, and MUD characters.
Content in threads you started may also be removed.

To proceed, choose `DELETE ACCOUNT` and type your username. Choose `CONFIRM`,
select the final `CONFIRM` box, and choose `DELETE`.

::: warning
Account deletion takes effect immediately and cannot be undone. The SYSOP
cannot restore a deleted account.
:::

The account that set up the station cannot be deleted in this way.

## 5. A First Session

This short session introduces the services most members use daily. It assumes
you have an account and that the station offers the usual services.

1. **Sign in.** Open the station's address, choose `LOGIN`, and sign in as
   described in Section 2.4. The STATION page appears.
2. **Look around.** Read the **STATION ACTIVITY** panel to see what has
   happened recently. Choose `ANNOUNCEMENTS` to read any notices from the
   SYSOP.
3. **Complete your profile.** Choose `[ PROFILE ]`, then `[ EDIT PROFILE ]`.
   Write a short Status line, such as `New here - say hello`, and choose
   `UPDATE MY PROFILE`.
4. **Read the bulletin board.** Choose `BULLETIN BOARD`. Choose any subject to
   read it and its replies. To answer, type in **Post reply** and choose
   `SEND REPLY`.
5. **Say hello in chat.** Choose `CHAT`, type a greeting in the box at the
   bottom, and press Enter.
6. **Send a letter.** Choose `USERS`, find another member, and choose `MAIL`.
   Enter a subject and message and choose `DELIVER PRIVATE MAIL`.
7. **Turn on the radio.** In the radio player at the edge of the screen, choose
   `POWER: OFF` to switch it on, then choose `SCAN >` to find a station that is
   on the air.
8. **Disconnect.** When you have finished, open `STATION` and choose
   `[ DISCONNECT ]`.

<!-- pagebreak -->

# Part II. Communication and Community

## 6. Users and Profiles

### 6.1 The User Directory

The `USERS` tab lists every registered member. Each entry shows the member's
photo, `ONLINE` or `OFFLINE`, username, callsign, status line, and the time last
active. SYSOPs are marked `SYSOP`.

To change the order, choose an option under **SORT BY**: **Most recently
active**, **Newest registered**, **Oldest registered**, or **Alphabetical
(A–Z)**. Then choose `SORT`. Long lists are divided into pages; use `PREVIOUS`,
`NEXT`, or a page number.

Each entry offers `PROFILE` and `MAIL`. Members who are online also offer
`DIRECT CHAT`.

### 6.2 Contacting Another Member

From another member's profile you may:

- Choose `OPEN DIRECT CHAT`, if the member is online (Section 7.2).
- Choose `SEND PRIVATE MAIL` (Chapter 8).
- Report the member's behaviour with `REPORT USER` in the profile's `▾` menu (Section 3.3).

### 6.3 User Feeds

Where the station offers user feeds, each profile has a **feed** of short
posts beneath the profile details.

**To post** to your own feed, open your profile, type up to 200 characters in
**Share a thought**, and choose `POST TO YOUR FEED`.

**To react** to a post, choose one of the five reaction buttons beneath it
(`✓`, `XD`, `<3`, `;_;`, `>:c`). You may hold one reaction per post; choosing
another replaces it.

**To comment**, choose `COMMENT` beneath the post, type up to 500 characters,
and choose `COMMENT` again. When you comment on another member's post, that
member receives an alert (Chapter 29). A profile shows the three newest comments
on each post; choose `VIEW ALL COMMENTS` to see the rest.

You may delete your own posts and comments from their ▾ menus. The station
limits the rate of posting: five posts in ten minutes, twenty comments an hour,
and sixty reaction changes an hour.

## 7. Chat

### 7.1 Station Chat

Station chat is a single public conversation open to every member. Its
messages are kept, so you can read what was said before you arrived.

1. Choose `CHAT`. The most recent messages are shown, and new ones appear
   automatically.
2. Type your message, up to 1,000 characters, in the box labelled
   **Transmit as**.
3. Press Enter, or choose `SEND`. Press Shift+Enter to begin a new line
   without sending.

To report a public message, use the report control shown on the message.

### 7.2 Direct Chat

Direct chat is a private, temporary conversation between two members who are
both online.

**To start a direct chat**, choose `DIRECT CHAT` beside the member's name in
the user directory, or `OPEN DIRECT CHAT` on their profile. Type and send
messages as in station chat.

**When someone writes to you**, a count appears on the `CHAT` tab and an alert
naming the sender appears near the top of every page. Choose the sender's name
to open the conversation.

::: note
Direct chat is not stored. The conversation is erased when either member
disconnects, and it cannot be recovered later. Use private mail for anything
you wish to keep.
:::

If a SYSOP has muted your chat, the message `Your chat is muted` appears and
messages cannot be sent until the restriction ends.

## 8. Private Mail

Private mail is a permanent, private message service between members of the
station.

### 8.1 Reading Mail

Choose `MAIL`. The **Inbox** lists messages sent to you, with unread messages
highlighted. A count on the `MAIL` tab shows how many messages are unread.
Choose `SENT MAIL` to list messages you have sent.

Choose a message to open it. Messages between you and another member are shown
together as one conversation, oldest first.

### 8.2 Sending Mail

1. Choose `COMPOSE +`. (Or choose `MAIL` beside a member's name.)
2. Select the **Recipient** from the list of registered members.
3. Enter a **Subject** of up to 120 characters.
4. Type the **Message**, up to 10,000 characters.
5. Choose `DELIVER PRIVATE MAIL`.

### 8.3 Replying and Deleting

To reply, open the conversation, type in the reply box at the bottom, and
choose `SEND REPLY`. To remove a message from your own mailbox, choose `DELETE`
beside it. Deleting a message does not remove it from the other member's
mailbox.

Mail is for messages between members. Station notices, such as feed comments,
SYSOP warnings, and password reset messages for SYSOPs, arrive as alerts
instead (Chapter 29). Feed-comment notices that earlier versions delivered as
mail are moved into alerts when the station is upgraded.

## 9. Bulletin Board, Announcements, and Polls

### 9.1 The Bulletin Board

The bulletin board holds public discussions, called *bulletins*, each with
its own replies. Choose `BULLETIN BOARD` to see the list. Each row gives the
author, the subject with an excerpt, and the number of replies. A `POLL` tag
marks a bulletin that includes a poll.

**To read** a bulletin, choose its subject.

**To reply**, type in **Post reply** at the foot of the bulletin and choose
`SEND REPLY`.

**To post** a new bulletin:

1. Choose `NEW BULLETIN +`.
2. Enter a **Subject** (3 to 120 characters) and a **Message** (up to 5,000
   characters).
3. Optionally, open `ADVANCED` to add a poll or set an automatic removal date
   (Sections 9.3 and 9.4).
4. Choose `POST`.

You may delete your own bulletins and replies from their ▾ menus. Deleting a
bulletin also deletes all of its replies.

### 9.2 Announcements

The `ANNOUNCEMENTS` tab carries official notices from the SYSOP. Only SYSOPs
may post announcements; everyone, including guests, may read them. An
announcement may include a poll.

### 9.3 Polls

A poll is a question with up to ten responses, attached to a bulletin or
announcement when it is first published.

**To vote**, select a response (or several, if the poll is marked
`MULTIPLE VOTES ALLOWED`) and choose `VOTE`. Results are shown after you have
voted. You may later choose `CHANGE VOTE` or `CLEAR VOTE`.

**To add a poll** when posting, open `ADVANCED`, select **Add a poll**, enter
the **Question** and responses, and if desired select **Allow multiple votes**.
The author of a poll may end voting with `CLOSE POLL`.

### 9.4 Automatic Removal

Under `ADVANCED`, the **AUTO-REMOVAL** fields let you choose a date and time
at which a bulletin will be removed automatically. The time zone defaults to
your device's own. Leave the date and time empty to keep the bulletin
indefinitely. Bulletins with a removal date show `AUTO-REMOVES` and the time.

## 10. Imageboard

The imageboard is a single station-wide board of picture threads. It is
available only to signed-in members.

### 10.1 Browsing

Choose `IMAGEBOARD` to see the **Thread catalog**. Each thread shows a preview
image, subject, author, number of replies, and the time it was last *bumped*
(that is, last replied to). Pinned threads appear first. Choose a thread's
picture or subject to open it, and choose any picture in a thread to see it at
full size.

### 10.2 Starting a Thread

1. Choose `NEW THREAD +`.
2. Enter a **Subject**. A **Message** is optional.
3. Choose an **Image**. PNG, JPEG, GIF, and WebP files of up to 10 MB are
   accepted. Animated images are shown by their first frame.
4. Choose `POST THREAD`.

### 10.3 Replying

Beneath the thread, type a **Message**, attach an **Image**, or both, and
choose `POST REPLY`. A thread marked `LOCKED` accepts no replies.

### 10.4 Limits

The SYSOP sets the number of threads the board keeps. When a new thread would
exceed the limit, the oldest threads are removed with their pictures. The SYSOP
may also limit the number of pictures in each thread; after the limit is
reached, text replies are still accepted.

SYSOPs, and members granted imageboard moderation, may pin, lock, and delete
threads.

## 11. Events

The events calendar announces station activities and lets members say whether
they will attend.

### 11.1 Viewing Events

Choose `EVENTS`. Upcoming events and events in progress are listed first,
followed by past and cancelled events. Choose an event to see its description,
start and end times in your own time zone, location, link, a countdown, and
the members who are going or interested.

### 11.2 Responding

On the event page, choose `GOING` or `INTERESTED`. The chosen button is
marked with a tick. To change your mind, choose the other button, or
`WITHDRAW`.

### 11.3 Creating an Event

The SYSOP decides who may create events: SYSOPs only, members of selected
groups, or all members. If you are permitted, `CREATE EVENT +` appears on the
EVENTS page.

1. Choose `CREATE EVENT +`.
2. Enter a **Name** and **Description**.
3. Enter the **Start date** and **Start time**, and optionally the end date and
   time.
4. Check the **Timezone**. It defaults to your device's time zone and is given
   in the standard form `Region/City`, for example `America/Edmonton`.
5. Optionally enter a **Location** and a **URL**.
6. Choose `CREATE EVENT`.

The organizer may later choose `EDIT` or `CANCEL EVENT`. A cancelled event
remains listed so that members know it will not take place.

## 12. File Archive

The file archive is the station's shared collection of downloadable files.
Choose `FILES` to open it.

### 12.1 Finding and Downloading Files

The archive is divided into five categories: **General**, **Documents**,
**Images**, **Software**, and **Other**. Choose `ALL` or a category name to
filter the list. Each entry shows the category, size, name, description,
uploader, and date. Picture files show a small preview.

Choose `DOWNLOAD` to save a file to your device.

### 12.2 Uploading Files

1. Choose `UPLOAD FILE +`.
2. Choose the **File**, select a **Category**, and add a **Description** of up
   to 500 characters.
3. Choose `UPLOAD TO ARCHIVE`.

The heading of the archive shows how much of the station's total storage is in
use, and the upload page shows the largest file accepted. The SYSOP may limit
uploading to SYSOPs or turn it off entirely, in which case `UPLOAD FILE +` does
not appear.

You may delete files you uploaded from their ▾ menus.

::: caution
Files in the archive are visible to every member. Do not upload private
documents or material you do not have the right to share.
:::

<!-- pagebreak -->

# Part III. Information and Entertainment

## 13. Radio

The StationNet radio is a shared receiver and transmitter. Members tune to
frequencies, measured in megahertz (MHz), to hear broadcasts and to talk with
one another by voice, Morse code, or still pictures. The radio is an internal
facility of the station; it does not use real radio equipment.

### 13.1 The Radio Player and the Radio Console

The radio can be operated in two places.

The **radio player** is docked on every page except the radio console. It
shows the power switch, the frequency, and what is playing. Choose `R` to
expand it and reveal tuning, scanning, volume, `MUTE`, and `FX` controls, and
`OPEN RADIO`. Because the player is present on every page, you may listen while
you use other services.

The **radio console**, opened with the `RADIO` tab or `OPEN RADIO`, offers
the complete set of controls: a frequency display, a band selector, a live
signal monitor, the **Station broadcast directory**, and controls for
transmitting.

### 13.2 Listening

1. Choose `POWER: OFF`. The switch changes to `POWER: ON`.
2. Tune a frequency in any of these ways:
   - Type a frequency, such as `7.25`, in the **Frequency MHz** field and
     choose `TUNE` (or press Enter).
   - Choose `TUNE − 0.50` or `TUNE + 0.50` to step down or up.
   - Choose a **Band** to move to it.
   - Choose `< SCAN` or `SCAN >` to search for the next active frequency.
   - Choose an entry in the **Station broadcast directory**.
3. Adjust **Volume**, or choose `MUTE`.

The display shows the name and message of the broadcast, the current title and
artist, and the band. `FX` adds an analogue receiver sound to what you hear;
choose it again to hear the plain signal.

::: note
Browsers will not play sound until you have clicked or tapped on the page. If
the radio is on but silent, choose any control on the page.
:::

### 13.3 Bands

The frequency range is divided into *bands*, each set aside for a particular
purpose and kind of transmission. A new station has the following bands; the
SYSOP may change them.

| Band | Range (MHz) | Use |
|---|---|---|
| BROADCASTS | 7.00–13.99 | Hosted programmes |
| MORSE / CW | 14.00–27.99 | Morse code |
| DATA TRANSMISSIONS | 28.00–144.99 | Still pictures (SSTV) |
| GENERAL COMMUNICATION | 145.00–432.99 | Live voice |
| EXPERIMENTAL / UNRESTRICTED | 433.00–999.99 | Morse and pictures |

Whether you may transmit on a band depends on the station's radio permissions:
`FULL` allows receiving and transmitting, `LISTEN` allows receiving only, and
`DENIED` allows neither. Only one person may transmit on a frequency at a time.

### 13.4 Transmitting

When you tune to a band on which you may transmit, the console shows the
controls for that band. If the band supports more than one kind of
transmission, choose one under **Active mode**.

**Live voice.** Hold `HOLD TO TALK [V]`, or hold the V key while you are not
typing in a field, and speak. Release to stop transmitting and clear the
frequency for others. The browser will ask permission to use the microphone the
first time. Voice travels directly between browsers and is never recorded.

::: note
Browsers permit microphone use only on secure connections. Live voice works at
the host computer itself, and from other computers only when the station is
reached through an `https://` address. The console reports this condition if it
applies.
:::

**Morse code (CW).** Choose `BEGIN CW TRANSMISSION`, then hold
`HOLD KEY [SPACE]` (or the Space bar) for each dot and dash. **Tone Hz** sets the
pitch and **Speed WPM** the speed in words per minute. Select **CW decoder** to
have received Morse shown as text. A **MORSE REFERENCE** table is provided.

**Pictures (SSTV).** Choose a **Local image**, check the preview, and choose
`TRANSMIT PICTURE`. Pictures are sent at a small size (80 by 60 pixels) line by
line. A picture you receive builds up in the **RX PICTURE** box; choose
`SAVE RECEIVED` to keep it.

### 13.5 Hosted Broadcasts

Members whom the SYSOP has authorized to host broadcasts see a `MANAGE
BROADCASTS` button and a **Broadcast console** on the radio console. A host
uploads audio files (MP3, OGG, WAV, or MIDI), arranges them in playlists or in
*programmes* that mix music with announcements, Morse, pictures, and live
microphone breaks, and starts the broadcast on an assigned frequency.

During a broadcast the host may choose `TAKE CONTROL` to act as disc jockey,
then `PAUSE`, `SKIP`, `HOLD TO TALK`, or `PLAY NOW` for prepared content.
Listeners who tune in midway join at the current point of the programme.

## 14. Television

The `TV` tab opens the station's television set. Television is *linear*:
each channel follows a schedule set by the SYSOP, and when you tune in you join
whatever programme is on the air at that moment, as with broadcast television.

### 14.1 Watching

1. Choose `TV`.
2. Choose `POWER` on the screen (or the ⏻ key on the receiver panel).
3. The set tunes to the station's default channel, or to the channel you
   last watched.

The receiver panel beside the screen provides channel up and down (`CH ▲ ▼`),
volume (`VOL − +`), `MUTE`, `GUIDE`, `INFO`, fullscreen, and a numeric keypad
for entering a channel number directly.

`INFO` displays the channel, the current programme with its progress, a
description, and the next programme. A channel with nothing scheduled shows an
`OFF AIR` test card with the time the channel resumes.

### 14.2 The Program Guide

Choose `GUIDE` (or press G) to display the program guide, a grid of all
channels against time. Use the arrow keys to move through it and choose a
programme, or press Enter, to read about it or tune to it. The guide's own
buttons are `Page Up`, `Page Down`, `+24 Hours`, and `−24 Hours`. Times are
shown in your local time.

### 14.3 Keyboard Controls

| Key | Action |
|---|---|
| 0–9 | Enter a channel number |
| ↑ ↓, Page Up, Page Down | Change channel |
| [ and ] | Volume down and up |
| M | Mute |
| G | Guide |
| I | Information |
| F | Fullscreen |
| Esc | Close the guide or information |

## 15. Videos

The `VIDEOS` tab contains video recordings that can be watched at any time.

To find a video, type part of its title in **Search videos by title** and
choose `SEARCH`, or browse the pages of the catalogue. Choose a video to open
it. Use the player's own controls to play, pause, seek, change volume, and view
fullscreen.

The SYSOP runs the video service in one of two modes. In **Community** mode,
members may upload videos and comment on them. In **Curated** mode, only the
station publishes videos, authors are not shown, and comments are not offered.

**To upload a video** (Community mode):

1. Choose `UPLOAD VIDEO +`.
2. Enter a **Title** and, optionally, a **Description**.
3. Choose a video file in MP4, WebM, or Ogg format, within the maximum size
   shown on the upload form.
4. Choose `UPLOAD VIDEO`.

The SYSOP sets the maximum size of each video separately from each member's
total storage quota. Member uploads must fit both limits; station uploads use
only the file size limit. The default file limit is 120 MB. Sizes use
1 MB = 1,048,576 bytes. Interrupted uploads must be restarted. **To comment**, type in
**Add a comment** beneath the video and choose `POST COMMENT`.

## 16. Library

The `LIBRARY` tab holds books in EPUB format and documents in PDF format.

### 16.1 Finding a Book

Use the controls at the top of the catalogue: **Search titles, authors, and
descriptions**, a **Category**, a **Format** (EPUB or PDF), and **Sort by**.
Choose `APPLY`; `CLEAR` restores the full catalogue. Choose a cover to see the
book's details.

### 16.2 Reading

On the details page, choose `READ BOOK` (EPUB) or `READ PDF`. If you have read
the book before, the button reads `RESUME READING` and returns you to where you
stopped. When the SYSOP permits it, a `DOWNLOAD` button saves a copy of the
file.

**EPUB books** are displayed one chapter at a time in the station's colours.
Select a **Chapter** from the list, or choose `PREVIOUS CHAPTER` and
`NEXT CHAPTER`. Choose `A−` and `A+` to change the size of the text.

**PDF documents** keep their original appearance. Choose ◀ and ▶ (or press the
Left and Right arrow keys) to turn pages, or type a page number. Choose − and +
to zoom, or `FIT WIDTH`.

### 16.3 Bookmarks and Reading Position

When you are signed in, your reading position is saved automatically and
follows you to any device you use on the same station.

**To set a bookmark**, choose `BOOKMARK` while reading. **To return to a
bookmark**, open **Bookmarks** above the text and choose the bookmark. **To
remove one**, choose × beside it. Guests may read, but positions and bookmarks
are not saved for them.

### 16.4 Contributing Books

In Community mode, members may choose `UPLOAD EPUB / PDF +` to add a book.
Title, author, and description are read from the file where available, and may
be corrected afterwards with `EDIT DETAILS`. Uploads count against a quota set
by the SYSOP.

## 17. WIKI Reference Library

The `WIKI` tab opens the station's **REFERENCE LIBRARY**: offline copies of
encyclopedias, dictionaries, manuals, and other reference works that the SYSOP
has installed. Each work is called a *collection*. Because the collections are
stored on the station itself, they are available without an Internet
connection.

### 17.1 Browsing and Searching

The opening page lists the available collections. Choose `OPEN COLLECTION →`
to go to a collection's main page, and follow links within its articles as you
would on any web site.

To search, select a collection in the search bar, type words in **Find
articles and pages**, and choose `SEARCH`. Choose a result to open it.

While reading, use `← BACK` and `FORWARD →` to retrace your steps, the
**COLLECTION** list to switch collections, and `ALL COLLECTIONS` to return to
the list.

If the page reports `LIBRARY UNAVAILABLE`, no collection is installed or the
reference reader could not start. Inform the SYSOP.

## 18. Programs

The `PROGRAMS` tab opens the **Program Manager**, a directory of software that
runs within StationNet: games, utilities, creative tools, and productivity
programs.

### 18.1 Finding and Launching a Program

1. Choose `PROGRAMS`.
2. Narrow the list by typing in **FIND PROGRAM**, or choose a category:
   `GAMES`, `UTILITIES`, `CREATIVE`, `EDUCATIONAL`, `PRODUCTIVITY`, `OTHER`,
   and, when Office is enabled, `OFFICE`.
3. Choose a program to see its details: version, author, the devices it
   supports, and the input it requires.
4. Choose `LAUNCH PROGRAM >`.

The list may also be operated from the keyboard: press ↑ and ↓ to select a
program and Enter to open it.

If your device lacks something the program needs, such as a keyboard, the
program pauses with an explanation. You may choose **Launch anyway** to try it
regardless.

### 18.2 Pinned and Recent Programs

Choose `PIN TO FAVORITES` on a program's details to add it to the **PINNED**
list at the left of the Program Manager. Programs you have launched appear
under **RECENTLY USED**. Choose `UNPIN` to remove a pinned program.

### 18.3 Running Programs

Programs open within the StationNet page. Some fill the whole page in
*immersive* presentation; choose **Exit immersive**, or press Esc, to return.
Many programs can also be shown fullscreen.

Programs save their own information, such as high scores and documents, to
your account on the station. Some keep shared station-wide records, such as a
community score table.

### 18.4 Programs Supplied with StationNet

StationNet is supplied with a set of built-in programs. A station may disable
any of them or add others.

| Program | Category | Description |
|---|---|---|
| Calculator | Utilities | Standard and scientific calculator with memory |
| Clock | Utilities | World clocks, alarms, timers, and stopwatch |
| Sleep Calculator | Utilities | Bedtimes and waking times by sleep cycle |
| Focus Console | Productivity | Work timer with session log and statistics |
| Personal Calendar | Productivity | Private month calendar and daily planner |
| ASCII/ANSI Art Studio | Creative | Coloured character art |
| MIDI Composer | Creative | Multi-track music composition and MIDI export |
| Breakout | Games | Brick-breaking game |
| Snake | Games | Classic, Sprint, and Maze Snake |
| Number Guess | Games | Number-guessing game |
| Light Cycles | Games | Light cycle arena game |
| Cybercrawler | Games | Turn-based grid exploration |
| Echo Choir: Remix | Games | Musical puzzles |
| Oddling Garden | Games | Virtual pets |
| Void Trader | Games | Space trading simulation |
| Wirestorm Rally | Games | Off-road racing |

## 19. Arcade

The `ARCADE` tab offers games for classic home consoles and arcade machines,
played in the browser through emulation. The SYSOP or the members supply the
game files; StationNet supplies the emulators.

### 19.1 Finding a Game

Use **Search games**, **Sort by**, and the **Platforms** and **Genres** menus,
then choose `APPLY FILTERS`. Choose a game's cover to open its page, which
lists its platform, genre, year, developer, and other details.

Supported platforms include the Nintendo Entertainment System, Super Nintendo,
Game Boy, Game Boy Color, Game Boy Advance, Nintendo 64, Nintendo DS, Sega
Genesis/Mega Drive, Master System, Game Gear, Atari 2600, 7800, and Lynx,
TurboGrafx-16, ColecoVision, MAME arcade sets, and Sony PSP.

### 19.2 Playing

Choose `▶ PLAY <title>`. The emulator loads and starts the game. The
emulator's own menu bar, at the foot of the game area, provides controls,
volume, fullscreen, and the emulator's settings, including the keyboard and
gamepad assignments.

::: note
The station does not keep emulator save states for you. If you use the
emulator's save-state feature, keep any file it gives you.
:::

Sony PSP games require a secure (`https://`) connection or play at the host
computer, and a browser with WebGL 2 support. Very large games may exceed the
memory available to a browser on small devices.

### 19.3 Uploading Games

When the arcade is in Community mode, members may choose `UPLOAD GAME +`,
select the platform, and supply the game file, cover picture, and details.
Uploads count against a quota.

::: caution
Upload only game files you are entitled to share.
:::

## 20. The Station MUD

The MUD is a text-based, multiplayer role-playing game. You explore a world
described in words, meet other players and the game's characters (called
NPCs), take on quests, gather possessions, and fight opponents. A station may
run several worlds, and each world is a separate game.

### 20.1 Choosing a World and Creating a Character

Choose `MUD`. The **Choose a world** page lists the worlds the station offers.
Each world requires its own character; your progress, skills, possessions, and
coins remain in the world where they were earned.

1. Choose `CREATE CHARACTER` beside a world.
2. Work through the steps, choosing `NEXT` and `BACK` as required:
   1. **Identity**: a character name of 2 to 32 letters. Spaces,
      apostrophes, periods, and hyphens are permitted. Other players see this
      name, which is separate from your StationNet username.
   2. **Race**: each race lists its traits and attribute adjustments.
   3. **Archetype**: your character's calling, with its own bonuses.
   4. **Attributes**: distribute the points shown among Strength,
      Agility, Intellect, and Vitality.
   5. **Skills**: select the number of starting skills shown.
   6. **Faction**: offered only in worlds that have factions.
   7. **Review**.
3. Choose `CREATE CHARACTER`.

Your choices cannot be changed later except by deleting the character with
`DELETE CHARACTER` and beginning again. To return to a world, choose
`CONTINUE`. To switch worlds, choose `CHANGE WORLD`.

### 20.2 The MUD Screen

The **GAME** panel carries a running account of what happens around you. At
its top are the name of your current location and three buttons: `ROOM` shows
the full description of the location with its exits, items, NPCs, enemies, and
other players; `LATEST` returns to the newest activity; and `CLEAR` empties the
account.

Below it is the **command line**. At the side, panels show your
**Character** (level, health, mana, experience, attributes), **Inventory**,
**Quests**, and, in some worlds, faction **Standing**. Choose a panel's title to
open or close it.

### 20.3 Entering Commands

Type in the command line and press Enter or choose `SEND`.

- Text without a leading slash is speech, heard by everyone in the same
  location.
- Text beginning with `/` is a command, such as `/look`.
- While you type a command, suggestions appear; press Tab to accept one.
- Press ↑ and ↓ to recall earlier commands.
- Enter `/help` for a list of commands, or `/help <command>` for details of
  one.

Where a name contains spaces, enclose it in quotation marks:
`/get "Red Apple" from "Oak Chest"`.

### 20.4 Moving About

Each location, or *room*, has exits leading to other rooms. `/exits` lists
them.

```
/go north        Leave by the north exit
/north  or  /n   The same, in short form
/look            Describe the room again
/map             Show a map of the surrounding area
```

The directions `north`, `south`, `east`, `west`, `northeast`, `northwest`,
`southeast`, `southwest`, `up`, and `down` may each be used as a command or
abbreviated (`/n`, `/ne`, `/u`, and so on). Some exits have names instead of
directions; use `/go <exit name>`.

In open country, `/travel <direction>` walks steadily in one direction, one
room at a time, until something of interest occurs; `/stop` halts the journey.
`/directions <place>` gives the way to a place you know, and `/destinations`
lists the places you know.

### 20.5 Talking to Players and Characters

| Command | Purpose |
|---|---|
| `/say <text>` | Speak to the room (plain text does the same) |
| `/tell "<name>" <text>` | Speak privately to one player |
| `/reply <text>` | Answer the last private message |
| `/emote <action>` | Describe an action |
| `/who` | List players in the world |
| `/talk <npc>` | Begin a conversation with an NPC |
| `/respond <number>` | Choose a reply in a conversation |
| `/block <name>` | Ignore a player |

### 20.6 Possessions

`/inventory` (or `/i`) lists what you carry and wear. `/get` and `/drop` pick
up and put down items; `/open`, `/close`, and `/put` work with containers;
`/equip` and `/unequip` wear or remove gear; `/use` uses or reads an item.
Where a merchant is present, `/shop` shows what can be bought and sold.

### 20.7 Quests

NPCs may offer quests. `/quests` lists your active quests and their
objectives, which are also shown in the **Quests** panel. Many worlds use
`/quest available`, `/quest accept`, and `/quest turnin` to find, accept, and
complete quests.

### 20.8 Combat and Recovery

Some NPCs are hostile. `/attack <enemy>` attacks; `/defend` reduces the
damage you take and protects your companions; `/flee` attempts escape;
`/cast <spell>` uses a known spell (see `/spells`); `/combat` reports the state
of the fight. Outside combat, `/rest` restores part of your health and mana.

If your character is defeated, it recovers at the starting location with half
its health. In ordinary worlds, part of the experience gained toward the next
level is lost, but levels are never lost.

As your character gains experience it rises in level and earns *attribute
points*, spent with `/train`, and *skill points*, used with `/learn` and
`/practice`. `/character` shows your full statistics and `/skills` your skills.

### 20.9 Dungeons and Expeditions

Some worlds contain dungeons that are generated afresh for each visit. Where
the station offers the procedural dungeon world, `/expedition` starts a random
expedition and `/daily` enters the station-wide Dungeon of the Day. Other
players may join an expedition with `/join <code>`. `/run` shows its progress,
`/complete` finishes it from the exit, and `/abandon` leaves at once.

::: note
The commands available depend on the world. Many worlds add banking, housing,
professions, vehicles, and other systems. `/help` always lists the commands
that apply where you are.
:::

### 20.10 Building Worlds with World Forge

Builders and the SYSOP create and edit worlds with **World Forge**, linked as
`WORLD FORGE` in the navigation bar and from each world's MUD page. The first
screen lists the station's installed worlds and any new worlds not yet
installed. `Create a world` starts a world with one arrival room;
`Import world ZIP` opens a world package as a new draft.

**Drafts.** Opening a world edits its *draft*, a copy kept on the station and
shared by every builder. Each change is saved to the draft at once; `UNDO`
and `REDO` step through recent changes, and `CHECKPOINT` (Ctrl+S) records a
backup that **Backups & recovery** can restore. Players see nothing until the
draft is published. Builders editing the same draft are named in the header.

**Tabs.**

- **World map.** The *Grid* view places rooms on numbered squares, draws
  passages, and paints wilderness terrain with the brush, rectangle, and
  ellipse tools. Layers hold upper and lower floors, joined by `up` and `down`
  passages. The *Connections* view diagrams rooms that have no grid position.
- **Content library.** Every kind of content: items, characters, dialogue,
  quests, factions, shops, professions, housing, vehicles, transit, triggers,
  dungeon templates, terrain presets, races, classes, skills, abilities, and
  effects. Selecting a record opens its guided editor; the *JSON* tab edits
  it directly. *Used by* lists what refers to it, and records still in use
  cannot be deleted.
- **Review.** Checks the draft for errors before it is published.
- **Handbook.** The creator handbook: every field, with examples.
- **Live world.** Acts on the running world: who is where, property holdings,
  vehicles, script state, and builder access. These actions take effect at
  once and do not change the draft.

**Publishing.** `PUBLISH` checks the draft and shows what will change. A draft
that only changes text and numbers is applied in place, and players notice
nothing but the new text. Adding, removing, or reconnecting content upgrades
the world: characters keep their levels, quests, reputation, and possessions;
characters standing in a removed room move to the starting room; items lying
in rooms return to their authored places; and dungeon runs in progress end.
A new world is *installed* hidden; the SYSOP enables it under **MUD service →
Worlds**. If a world is upgraded from Settings while a draft is open, the
draft is marked out of date and may be rebased from **Backups & recovery**.

::: note
The home world may be edited and published like any other. Take a station
backup (Section 40.1) before its first full publish, which converts the home
world to the package format.
:::

<!-- pagebreak -->

# Part IV. Office

## 21. Office Fundamentals

StationNet Office is a set of six private productivity programs:
**Documents**, **Sheets**, **Presentations**, **Draw**, **Agenda**, and
**BASIC**. Open them with the `OFFICE` tab, or from the `OFFICE` category in
the Program Manager. Office is available only to signed-in members.

### 21.1 The Office Password

Office work is *encrypted* in your browser before it is sent to the station.
Neither the SYSOP nor anyone else can read it. The encryption is controlled by
a single **OFFICE Password**, which is separate from your StationNet password.

**The first time** you open any Office program, you are asked to choose an
OFFICE Password of at least 12 characters and confirm it.

**Thereafter**, each program opens with an **Unlock** panel:

1. Enter your **OFFICE Password**.
2. Optionally select **Remember on this browser**, so that the programs stay
   unlocked on this browser until you choose `LOCK` or disconnect.
3. Choose the unlock button.

Unlocking one Office program unlocks all of them in the same browser tab.
Choose `LOCK` in any program when you leave your desk.

::: warning
The OFFICE Password is not stored anywhere and cannot be reset by the SYSOP. If
you forget it, everything you have saved in Office becomes permanently
unreadable.
:::

### 21.2 Operations Common to All Office Programs

Each program keeps a private list of your files, and each provides `NEW`,
`SAVE`, `DELETE`, `UNDO`, and `REDO` where they apply.

Documents, Sheets, Agenda, and BASIC save your work automatically shortly
after you stop typing, and report the save status on screen. In Presentations
and Draw, choose `SAVE` to save. If the same file is open in two browser tabs,
StationNet prevents the older tab from overwriting newer work.

**Import** and **export** take place entirely in your browser. Importing
creates a new Office file from a file on your device; exporting saves a copy of
an Office file to your device in a common format. Exported copies are not
encrypted.

## 22. Documents

**Documents** is a word processor.

**Writing and formatting.** Select a paragraph style (Normal, Title, Subtitle,
Heading 1 to 3, Quote, or Code) and use the toolbar for bold, italic,
alignment (`LEFT`, `CENTER`, `RIGHT`, `JUSTIFY`), bulleted and numbered lists,
indentation, links, horizontal rules, images, and tables (`TABLE`, `+ ROW`,
`+ COL`). `FIND` opens find and replace.

**Importing.** Choose `IMPORT FILE` and select a Word (`.docx`), OpenDocument
(`.odt`), text, Markdown, HTML, or RTF file.

**Exporting.** Choose `EXPORT`, then `WORD .DOCX`, `ODT`, `PDF`, `HTML`,
`MARKDOWN`, or `PLAIN TEXT`.

**Printing.** Choose `PRINT`. `PRINT THEME` previews the page as it will print,
in black on white.

::: note
Word and OpenDocument files keep their general structure, but complex page
layouts, tracked changes, comments, and unusual fonts may be lost on import or
export. Markdown and plain text lose page styling; plain text also loses
pictures.
:::

## 23. Sheets

**Sheets** is a spreadsheet program. Each workbook may contain several sheets.

**Entering data.** Select a cell and type a value or a formula. Formulas
begin with `=`, for example `=SUM(B2:B10)`. Set number formats (`GENERAL`,
`NUMBER`, `CURRENCY`, `PERCENT`, `DATE`) and alignment from the toolbar.

**Arranging data.** Use `+ ROW`, `− ROW`, `+ COL`, and `− COL` to insert and
remove rows and columns; `FILL →` and `FILL ↓` to copy across or down;
`SORT ↑` and `SORT ↓` to sort; `FILTER` to show only matching rows; and
`FREEZE` to keep headings in view. `RENAME` and `DELETE SHEET` act on the
current sheet.

**Charts.** Select a range and choose `BAR CHART`, `LINE CHART`, or
`PIE CHART`.

**Importing and exporting.** `IMPORT FILE` accepts Excel (`.xlsx`, `.xls`),
OpenDocument (`.ods`), CSV, and TSV files. For text files you may choose the
character encoding and separator. `EXPORT` offers `EXCEL .XLSX`, `ODS`, `CSV`,
`TSV`, and `PDF / PRINT`.

## 24. Presentations

**Presentations** prepares and shows slide presentations.

**Building slides.** Choose a layout (`BLANK`, `TITLE`, `TITLE + BODY`,
`TWO COLUMNS`) and a theme (`STATION`, `LIGHT`, `DARK`, `WARM`). Add objects
with `+ TEXT`, `+ IMAGE`, `+ RECT`, `+ ELLIPSE`, `+ LINE`, and `+ TABLE`.
Selected objects may be moved, resized, aligned, sent to the `FRONT` or
`BACK`, duplicated (`DUP`), or deleted. Each slide may have a background
picture and a transition (`NONE` or `FADE`).

**Presenting.** Choose `PRESENT`. Advance with the Right arrow, Page Down, or
the Space bar; go back with the Left arrow; press Esc or choose `EXIT` to
finish.

**Importing and exporting.** `IMPORT .PPTX` opens a PowerPoint file.
`EXPORT .PPTX`, `PDF`, and `SLIDE .PNG` save the presentation or the current
slide.

Remember to choose `SAVE`; Presentations does not save automatically.

## 25. Draw

**Draw** produces diagrams, flowcharts, posters, and simple page layouts.

Select a tool: `SELECT`, `PEN` (freehand), `LINE`, `ARROW`, `CONNECTOR`,
`RECT`, `ELLIPSE`, `TRIANGLE`, `DIAMOND`, `TEXT`, or `IMAGE`. Drag on the page
to draw. Connectors attach to shapes and follow them when the shapes are moved.

With objects selected, `GROUP` and `UNGROUP` combine and separate them,
`FRONT`, `FORWARD`, `BACKWARD`, and `BACK` change their stacking, and
`DUPLICATE` and `DELETE` copy and remove them. The zoom list sets the view from
50% to 200%, or `FIT`.

To export, choose `PNG` for a picture or `SVG` for a scalable drawing. Choose
`SAVE` to keep your work in Office.

## 26. Agenda

**Agenda** combines a calendar, a task list, and an address book.

**Calendar.** Choose `MONTH`, `WEEK`, or `DAY`, and use `‹`, `›`, and `TODAY`
to move through time. Choose `+ EVENT`, enter the details, and choose
`SAVE EVENT`. Events may repeat.

**Tasks.** Choose `TASKS`, then `+ TASK`. Tasks may have subtasks
(`+ SUBTASK`). Choose `SAVE TASK`.

**Contacts.** Choose `CONTACTS`, then `+ CONTACT`, and `SAVE CONTACT`.

**Reminders.** Choose `ENABLE REMINDERS` and allow notifications when your
browser asks. Reminders are given only while Agenda is open and unlocked in a
browser tab.

## 27. BASIC

**BASIC** is a programming environment for the BASIC language, compatible with
programs written for QBasic and QB64. Programs can draw graphics, play sound,
and respond to the keyboard, mouse, and touch screen.

### 27.1 Writing and Running a Program

1. Choose `NEW`.
2. Type the program in the `CODE` view. For example:

   ```
   CLS
   INPUT "What is your name"; N$
   PRINT "Hello, "; N$
   ```

3. Choose `RUN ▶`. The program runs in the `SCREEN` view.
4. Choose `STOP ■` to end a program that is still running.

Error messages appear in the console beneath the screen. To study working
programs, select one from **Open an example…**.

Each run takes place in a closed area of the browser that cannot reach the
station, your Office files, or the network.

### 27.2 Importing and Exporting

`IMPORT .BAS` opens a BASIC source file from your device. `EXPORT…` offers two
forms:

- **BASIC file (.bas)**: the program text.
- **StationNet Package (.zip)**: an installable program. Fill in the name,
  description, category, and device requirements. Give the file to the SYSOP,
  who can install it in the Program Manager for all members.

<!-- pagebreak -->

# Part V. Personalization and Everyday Use

## 28. Appearance and Sound

### 28.1 Themes

StationNet's appearance is governed by two colour schemes: the
**site-wide theme**, which colours pages, panels, and controls, and the
**masthead theme**, which colours the station name at the top of the screen.
The SYSOP chooses the station's defaults. Supplied themes include Default/Cyan,
Amber, Terminal Green, Bright Green, Magenta, Electric Blue, Crimson, Violet,
Monochrome, and Vintage Terminal. Themes added by the SYSOP are marked
`[CUSTOM]`.

To choose your own:

1. Open your profile and choose `[ EDIT PROFILE ]`.
2. Under **Theme preferences**, choose a **Site-wide theme** and a
   **Masthead theme**. The preview panels show the effect. Choose
   **Station default** to follow the station.
3. Choose `SAVE THEME PREFERENCES`.

If **Theme preferences** does not appear, the SYSOP has required all members
to use the station's themes.

### 28.2 Interface Switches

The switches on the station line apply to the browser you are using and are
remembered by it.

`SFX`
: Interface sound effects, such as clicks and the alerts for new mail and
  direct chat.

`VFX`
: A scan-line effect resembling a cathode-ray screen.

`MUSIC`
: The station theme tune, if the SYSOP has provided one. Your choice is saved
  with your account.

Where supported, a fullscreen switch is also shown.

### 28.3 Accessibility

All pages can be operated from the keyboard. A **Skip to content** link at
the top of each page moves directly to the work area. Moving tagline effects
are stopped when your device is set to reduce motion. Text size follows your
browser's zoom setting (Ctrl and + or −).

## 29. Notifications

StationNet notifies you within the station's pages:

- A count on the `ALERTS` tab shows unread alerts, with a sound if `SFX` is on.
  You receive an alert when someone replies to your bulletin or imageboard
  thread, or to a bulletin thread you replied to; mentions you as
  `@username` in a bulletin, reply, feed post, feed comment, or oneliner;
  comments on or reacts to your feed post; or sends you a MUD tell while you
  are away from the MUD. You are also reminded of an event you answered
  `GOING` or `INTERESTED` to once it is less than a day away, told when a
  report you filed has been reviewed, and receive SYSOP warnings and notices.
  Repeated alerts about the same thread or post are combined into one line.
- A count on the `MAIL` tab shows unread mail, with a sound if `SFX` is on.
- A count on the `CHAT` tab and an alert at the top of the page announce a
  direct chat message. The alert clears when you open the conversation.
- The **STATION ACTIVITY** panel summarizes new bulletins, files, members,
  broadcasts, and television.
- Agenda reminders use your browser's notifications (Chapter 26).

Open `ALERTS` to read your alerts. Choose an alert to mark it read and go to
what it concerns; `MARK ALL READ` and `CLEAR READ` tidy the list. Under
**Alert preferences**, clear the kinds of alert you do not want. SYSOP notices
and system messages are always delivered.

Notifications appear only while a station page is open in your browser.

## 30. Installing the Station as an Application

A station can be installed on a phone, tablet, or computer so that it opens in
its own window with its own icon, like any other application.

1. Open the station in a browser that supports installation, such as Chrome
   or Edge, or Safari on an iPhone or iPad.
2. Choose the browser's **Install** or **Add to Home Screen** command.

The installed application uses the station's name and, if the SYSOP has
chosen, its icon. A connection to the station is still needed; when the station
cannot be reached, the application shows **Station unavailable**.

::: note
Browsers offer installation only for secure (`https://`) addresses and for the
host computer itself. A station reached through a plain `http://` network
address can be used in the browser but cannot be installed.
:::

On a phone, all services can be used in the browser. Some programs and games
show on-screen touch controls.

## 31. Station Hours

Some stations are open only at certain times. When a station keeps hours, a
line beneath the station line shows them, for example
`STATION HOURS: 8:00AM - 9:00PM`. Other messages that may appear there are
`HOURS TEMPORARILY EXTENDED` and `OPEN EARLY`.

When the station closes, it stops, and pages no longer respond until it opens
again. Save your work and finish conversations before closing time.

## 32. Account Security

Observe the following practices:

- Choose a password of at least ten characters that you do not use elsewhere.
- Use **Remember me on this device** and Office's **Remember on this browser**
  only on your own devices.
- On a shared computer, choose `LOCK` in Office and `[ DISCONNECT ]` when you
  finish.
- If you suspect someone else has used your account, change your password at
  once (Section 4.4) and inform the SYSOP.

SYSOPs can read the station's public services and administer accounts. They
cannot read your Office files. Private mail and chat are stored on the station
computer and should not be regarded as secret from the person who operates it.

<!-- pagebreak -->

# Part VI. Station Hosting and Administration

This part is for the SYSOP. It describes how to install StationNet, start and
operate stations, and administer accounts and content.

## 33. Installation

### 33.1 The Software, the Station, and Its Data

Three things should be kept distinct:

- **StationNet** is the software: the program and its supplied files. It
  never writes to its own folder.
- A **station** is one configured, running service with its own name and port.
- A station's **data** is kept in its own folder under `stations/`. Every
  account, message, upload, installed program, theme, world, and reference
  collection belongs to the station folder.

A typical installation is arranged as follows:

```
StationNet/
  run.py, stationnet/, .venv/   the software (packaged: core/)
  stations/
    default/                    one folder per station
      station.json              name, port, network access, hours
      station.db                the station database
      uploads/, themes/, ...    files, programs, worlds, WIKI data
      run/                      status and log while running
```

### 33.2 Requirements

The host computer may run Windows, Linux, macOS, or Raspberry Pi OS. Source
installations require Python 3.11 or newer. Packaged Windows builds include
everything they need. Packaged Linux and Raspberry Pi builds require Python
3.11 with venv. The Station Manager requires Tkinter (`python3-tk` on Debian
and Raspberry Pi OS). StationNet uses no separate database server.

### 33.3 Installing a Packaged Release

**Windows.** Unzip the package and double-click `install.bat`. The software is
installed to `%LOCALAPPDATA%\StationNet`. Start a station with `start.bat` in
that folder, or manage several with `manager.bat`. To run without
installing, run `start.bat` from the unzipped folder.

**Linux and Raspberry Pi.** Unzip the package for your platform and run:

```
sh install.sh
~/.local/share/stationnet/start.sh
```

The Station Manager is `~/.local/share/stationnet/manager.sh`. Packaged
releases install without network access.

**Android.** The StationNet Android application hosts a station on a 64-bit
ARM phone running Android 8 or later. Tap **Start station**, complete the
first-run setup in the phone's browser, then enable the phone's hotspot so that
nearby devices can connect to the addresses shown. The Android application does
not yet include the WIKI reader.

### 33.4 Installing from Source

**Windows:** double-click `install.bat`, then start with `run.bat` (one
station) or `manager.bat` (several stations).

**Linux and macOS:**

```
chmod +x install.sh run.sh manager.sh uninstall.sh
./install.sh
./run.sh
```

Source installation downloads Python packages and the WIKI reader.

### 33.5 First-Run Setup

When a new station starts for the first time, open `http://127.0.0.1:8080`
on the host computer. The **Initialize local station** page asks for:

- The **Station name** (2 to 80 characters) and **Station description**.
- The **Administrator username** and **Password** of the first SYSOP.
- The **Registration policy**: Open, Private, or Disabled.

Choose `INITIALIZE STATION`. The station and its SYSOP each receive a callsign,
and the SYSOP is signed in.

::: caution
The account created at first-run setup is the station's only SYSOP account.
Record its password safely. StationNet provides no means of creating further
SYSOP accounts through the station's pages.
:::

## 34. Starting, Stopping, and the Station Manager

### 34.1 Running a Single Station

`run.bat`, `./run.sh`, or a packaged `start.bat`/`start.sh` starts the
`default` station in a console window. Press Ctrl+C in that window to stop it.
To run another station from the command line, give its identifier:

```
python run.py --station <id>
```

A station that is already running cannot be started a second time.

### 34.2 The Station Manager

The Station Manager (`manager.bat` or `manager.sh`) is a desktop window that
lists every station under `stations/` with its name, status, port, and
addresses. Each station card provides:

`Start`, `Stop`, `Restart`
: Control the station. Stations started here continue running if the manager
  window is closed. A station that has not stopped within 20 seconds offers
  `Force Stop`.

`Open`
: Opens the station in the browser.

`Logs`
: Opens the station's log.

`Settings`
: Changes the name, port, network access, and the **Served through an HTTPS
  proxy** option. Changes to a running station take effect when it restarts;
  `Save & Restart` does both.

`Hours`
: Sets opening and closing times (Chapter 36).

`Delete`
: Removes the station permanently, after you type its name to confirm.

**To create a station**, choose `+ New Station`. The manager suggests a name
and a free port and asks who may connect. It can also create the first SYSOP
account, so that the browser setup page is not needed.

A station shown as **CRASHED** stopped unexpectedly; its last log line is
displayed. Start it again, and examine the log if the problem recurs.

## 35. Network Access

By default a station listens on every network adapter of the host, on port
8080. Other devices connect to `http://<host-address>:8080`. If they cannot,
permit incoming TCP connections on the station's port in the host's firewall.

In the Station Manager's **Settings** you may restrict a station to **this
computer only**, to all adapters, or to one chosen address. For source
installations, the environment variables `STATIONNET_HOST` and
`STATIONNET_PORT` override the station's settings.

### 35.1 Secure Connections

Browsers allow the microphone (radio voice), application installation, and PSP
emulation only on secure connections. To provide them to other computers, place
StationNet behind an HTTPS reverse proxy with a certificate trusted by every
client. For example, with Caddy:

```
stationnet.lan {
    tls internal
    reverse_proxy 127.0.0.1:8080
}
```

Set the station to listen on this computer only, select **Served through an
HTTPS proxy** in the Station Manager (or set `STATIONNET_HTTPS=1`), make
`stationnet.lan` resolve to the host, and install the proxy's root certificate
on each client.

::: caution
Enable the HTTPS proxy setting only when clients actually use the `https://`
address. With it enabled, sign-in does not work over plain `http://`.
:::

Live voice travels directly between browsers and is designed for clients on
the same local network.

## 36. Station Hours, Sleep, and Wake

Station hours are set from the Station Manager's `Hours` button and take
effect at once. They are kept only while a Station Manager window is open
(it may be minimized).

- **Closing.** For a few minutes before closing (five by default) the card
  shows a countdown and a `Stay Online` button, which keeps the station open
  until its next opening time. Otherwise the station is stopped cleanly at
  closing time.
- **Opening.** A stopped station is started at its opening time. A station
  you stop by hand during its hours stays stopped until the next opening.
- **Messages.** The header line, and the *Extended* and *Early Open* messages,
  may be edited.
- **Sleep.** With SLEEP selected, the computer is put to sleep after the
  station closes, following a `Cancel Sleep` countdown, unless another station
  is still running.
- **Wake.** The manager sets a wake timer before the earliest next opening.
  **Sleep & Wake** in the manager's toolbar sets the countdown and lead time and
  shows the next scheduled wake.

On Windows, wake timers must be allowed in **Power Options → Sleep → Allow wake
timers**, and some computers using Modern Standby ignore them. On Linux and
macOS a `wake_command` must be configured in `manager.json`; see the README.

## 37. SYSOP Control: Station Settings

The `SYSOP CONTROL` tab opens the station settings. A sidebar on the left lists
the categories; choose one to show its settings, and choose an entry beneath it
to jump to a particular card. On a narrow screen the categories appear as a row
of buttons above the settings. **Find a setting**, at the top of the sidebar,
searches every card by name, field label, or member name; press Enter to open
the first result.

| Category | Contents |
|---|---|
| Overview | Station status at a glance and links to the management consoles |
| Identity & Appearance | Station name and description, landing page message, station icon, site theme, masthead and ASCII banner, header taglines, theme library, station MIDI theme |
| Access & Members | Registration and guest access, invitations, permission groups, group membership |
| Modules | The module switchboard |
| Community | Login sequence, Events, User Feeds, Imageboard, Office |
| Media & Content | File archive, Videos, Library, Arcade, WIKI |
| Radio & Broadcasting | Band allocations, hosted broadcasting, broadcaster storage |
| Television | TV settings and the link to TV management |
| MUD | General settings, worlds, races and classes, skills, abilities and effects, dungeon themes, Dungeon of the Day |

**Saving.** Each form has its own save button directly beneath the fields it
saves, and nothing else is saved with it. After saving, the result appears at
the top of the same card. Blocks marked `APPLIES IMMEDIATELY` (such as
generating an invitation, enabling a world, or downloading a core) act as soon
as their button is pressed. A form that has been edited but not saved is
marked `UNSAVED CHANGES`, and its category shows an amber dot in the sidebar.
Moving between categories keeps the edits; saving a different form or leaving
the page asks for confirmation first. `CANCEL CHANGES` restores a form's saved
values.

**Module switchboard.** Select the services to offer and choose
`SAVE MODULE AVAILABILITY`. A disabled module disappears from navigation, but
its data is kept and its settings can still be changed. Each service's
settings card shows whether the module is on, with a link back to the
switchboard; this is the only place modules are turned on or off.

**Identity & Appearance.** Here you set the station name and description; the
landing page message (Markdown is accepted); the station icon (and whether it
is used for the installed application); the site theme, and whether members
may choose their own; the masthead theme and the ASCII banner text, lettering
style, size, and colour, with a live preview; the header taglines and their
colours and effects; and a station MIDI theme tune and its volume. Custom
colour themes are imported from JSON files, or removed, in the **Theme
library**.

**Registration and guest access.** Choose the registration mode (Open,
Private, or Disabled) and whether guests may browse. For a private station,
choose `GENERATE INVITATION`. The complete invitation code and link are shown
once only; send them to the new member. Unused invitations may be revoked.

**Login sequence.** Under Community, **Login sequence** turns the screen
members see after signing in (Section 2.6) on or off, chooses its panels and
their order by position number, and holds the **Quotes of the day**: one per
line, up to 100 quotes of 200 characters. When no quotes are entered, a header
tagline is shown instead. A SYSOP may remove any oneliner with its `[X]`
button; each removal is recorded in the moderation audit history.

**Service settings.** The Community, Media & Content, Radio & Broadcasting,
Television, and MUD categories configure each optional service. The file
archive policy (who may upload, total storage, and largest file) is under
Media & Content. These services are described in Chapter 39.

**Management consoles.** Moderation, TV management, automated broadcasts,
Programs, announcements, and World Forge have their own pages. They are
linked from the Overview and from the foot of the sidebar.

## 38. Accounts, Permissions, and Moderation

### 38.1 Delegating Duties

There is one SYSOP account. Duties are delegated to members through *groups*,
which are managed under **SYSOP CONTROL → Access & Members**:

- **Permission groups.** Create a group with `CREATE GROUP`. Give it
  `HOST BROADCASTS` (radio hosting) or `MODERATE IMAGEBOARD` (pin, lock, and
  delete on the imageboard), and add radio access rules (`FULL`, `LISTEN`, or
  `DENIED` for a band, range, or frequency).
- **Group membership.** Open a member and select the groups they belong
  to. Every member also belongs to the permanent `DEFAULT` group. Across
  groups, `DENIED` overrides `FULL`, and `FULL` overrides `LISTEN`.
- **Events.** Event creation may be limited to selected groups.
- **MUD building.** In World Forge (**Live world → Builder access**), the
  SYSOP may authorize members as builders. Builders may edit and publish any
  authored world; only the SYSOP may change a world's progression or starting
  room, and new worlds stay hidden until the SYSOP enables them.

### 38.2 Moderation

The `MODERATION` tab lists accounts, current restrictions, **Open reports**,
and a searchable **Audit history**. For each report, read the reported item and
choose `RESOLVE` or `DISMISS` with a note.

Open an account to record an action. Actions are formal warnings (delivered
as an alert), temporary suspensions, permanent bans, chat mutes, and
reinstatement. A reason is required; private notes are visible only to SYSOPs.
Warned and muted members receive the reason as an alert, and the member who
filed a report is told when it is resolved or dismissed, with your note.
`DELETE USER CHAT` removes the member's recent public chat messages. The account
page also allows an account to be deleted.

A suspended or banned member cannot sign in and is shown the reason. Temporary
restrictions expire automatically.

### 38.3 Passwords

When a member uses `FORGOT PASSWORD`, the station sends a temporary password
to every SYSOP's `ALERTS`. Confirm the member's identity before passing it on.

## 39. Managing Modules and Content

**Bulletins and announcements.** Remove any bulletin, reply, or chat message
from its ▾ menu. Post announcements with `NEW ANNOUNCEMENT +`.

**Videos, Library, and Arcade.** Each has a **Community** mode (members
upload, within per-member quotas) and a **Curated** mode (station uploads
only). Set the default quota and per-member overrides, and upload station
material, in the service's settings card. The Videos module also has a maximum video
file size setting for member and station uploads, configurable from 1 to
1,048,576 MB. The Library has categories, an
option to allow downloads, and a maximum file size. The Arcade section manages
emulator *cores*: each platform's preferred core can be chosen, and a missing
core can be downloaded while the host has Internet access. PSP uploads have
their own size limit.

**Television.** Set the schedule time zone, upload size limit, and default
channel, then choose `OPEN TV MANAGEMENT`. There you create channels
(`+ NEW CHANNEL`), build the **Programming library** by uploading videos or by
copying them into the import folder and choosing `SCAN IMPORT FOLDER`, and
arrange programmes into *blocks* on the **Schedule**. Weekly blocks repeat;
one-off blocks override them. The **Auto scheduler** fills a channel's
schedule from categorized programmes; review the proposed schedule and choose
`PUBLISH`. Nothing goes on the air until published.

**Radio.** Set band names, ranges, and permitted transmission types under
**Band allocations**; assign each broadcast host a frequency and set the
maximum continuous session under **Hosted broadcasting**; and set audio
storage quotas under **Broadcaster storage**. `MANAGE AUTOMATED
BROADCASTS` schedules programmes to start at set times or intervals.

**WIKI.** Place `.zim` reference files in the station's `kiwix_data` folder
(the path is shown in the WIKI settings card) and turn WIKI on in the module
switchboard. New files are
found on the next visit. Install only collections from sources you trust.

**Programs.** Choose `PROGRAMS` under the management consoles (**Manage programs**).
Upload a program package ZIP and choose `VALIDATE + INSTALL`. Installed
programs can be disabled, re-enabled, or uninstalled. Built-in programs can
be disabled but not removed. Uninstalling keeps members' saved data.

**MUD.** Enable the MUD module, then enable individual worlds under **MUD
service → Worlds**. Worlds are imported from ZIP files; several may run at
once. The section also imports races, classes, skills, abilities, and dungeon
themes, and offers an example world to download. World Forge (Section 20.10)
builds and edits worlds in the browser.

## 40. Backup, Upgrade, and Maintenance

### 40.1 Backing Up

To back up a station, stop it and copy its entire folder under `stations/`.
That folder contains the station's identity, accounts, messages, uploads,
installed programs, themes, and worlds. Reference collections (`.zim` files) in
`kiwix_data` are also kept there and may be very large.

The Station Manager's `Delete` command offers **Back up the station first**,
which saves a ZIP of the folder, without `.zim` files. To restore a backup,
unzip it into `stations/`.

::: warning
A station folder contains every member's private mail and the station's secret
key. Store backups securely and never publish them.
:::

### 40.2 Upgrading

Back up `stations/` first. Stop every station. Then:

- **Windows package:** unzip the new release and run its `install.bat`. The
  software is replaced; `stations` is left alone.
- **Linux package:** unzip the new release and run its `install.sh`.
- **Source:** replace the source files, keeping `stations/`, and run the
  installer again.

Installations older than the `stations/` layout are migrated automatically on
first start. `python run.py --migrate-only --dry-run` previews the migration.

### 40.3 Uninstalling

`uninstall.bat` or `./uninstall.sh` offers to back up station data, then
removes the generated environment and station data, leaving the StationNet
software ready to be installed again.

### 40.4 Shutting Down Safely

Always stop a station with `Stop` in the Station Manager, or Ctrl+C in its
console window, before shutting down the host computer or copying the
station's folder. The station then closes its database cleanly. Continuous
radio broadcasts and similar activities are restarted automatically at the next
start.

<!-- pagebreak -->

# Part VII. Developer's Reference

This part introduces the means of extending StationNet. Detailed references
are listed in Chapter 45.

## 41. Project Organization

StationNet is a Python application built on Flask, served by Uvicorn, with
data in SQLite. Pages are rendered on the server, with JavaScript where needed.
No front-end build step is required to run it; the Office programs and some
other components are supplied as prebuilt bundles.

| Location | Contents |
|---|---|
| `run.py` | Starts a station |
| `stationnet/` | Application package: one module per service |
| `stationnet/modules.py` | Module switches and navigation names |
| `stationnet/db.py` | Database schema and migrations |
| `stationnet/templates/` | Page templates, one folder per service |
| `stationnet/static/` | Style sheets, scripts, and bundled assets |
| `stationnet/application_packages/` | Built-in programs and the program API reference |
| `stationnet/office_*/` | Sources of the Office programs |
| `tests/` | Automated tests |
| `packaging/`, `build-all.ps1` | Distribution builders |
| `android/` | Android host application |

Each service is a Flask *blueprint*. A new service is registered in the same
manner and should use the common page template (`base.html`) and the module
switch mechanism in `modules.py`.

## 42. Programs and Packages

The most direct way to extend a station is to write a program for the
Program Manager. A program is a ZIP file containing `manifest.json` and a
JavaScript entry point (or a BASIC `.bas` file). It runs in an isolated worker
with no access to the page, the network, or the file system. A minimal
manifest:

```
{
  "package_format": 1,
  "slug": "my-application",
  "name": "My Application",
  "description": "A short explanation.",
  "author": "Your name",
  "version": "1.0.0",
  "category": "Utilities",
  "entrypoint": "main.js"
}
```

and a minimal program:

```
StationNet.start((ui) => {
  ui.render(ui.title("Hello"), ui.text("Welcome."));
});
```

The runtime provides interface elements (text, menus, forms, tables,
calendars), a canvas with sprites and framebuffers, keyboard, pointer, touch
gamepad, and fullscreen support, sound and General MIDI playback, per-user and
shared storage, score tables, and multiplayer sessions. Packages are limited to
64 files and 5 MiB compressed. Place `manifest.json` at the root of the ZIP.

The SYSOP installs packages through **Manage programs**. The complete
reference is `stationnet/application_packages/APIREADME.md`.

## 43. Themes

A colour theme is a JSON file defining a **site** palette, a **masthead**
palette, or both. Start from the template copied to the station's `themes/`
folder (also `stationnet/theme_templates/template-theme.json`), give it a unique
ID, name, and author, and edit the colours. The SYSOP imports it under
**Station settings → IMPORT COLOUR THEME**. The format is described in
`stationnet/theme_templates/THEMES_README.md`. Built-in palettes are defined in
`stationnet/themes.py`.

## 44. MUD Worlds and Content

MUD worlds are distributed as ZIP files containing rooms, exits, items, NPCs,
dialogue, quests, triggers, wilderness, dungeons, and their own races, classes,
and skills. They may be built in World Forge and exported, or written
directly as files. The complete format reference is the creator handbook,
available from World Forge's **Handbook** tab. Races and classes, skills, abilities and effects, and
dungeon themes may also be distributed separately as JSON. Each kind has a
downloadable template in **SYSOP CONTROL → MUD**.

## 45. Testing, Building, and Further References

**Tests.** Install the development requirements and run the test suite:

```
python -m pip install -r requirements-dev.txt
python -m pytest
```

Browser-side tests are run with the `npm run test:*` scripts in
`package.json`.

**Rebuilding bundles.** After changing an Office program or the MIDI engine,
run `npm ci`, then the matching script: `npm run build:documents`,
`build:sheets`, `build:presentations`, `build:draw`, `build:agenda`,
`build:basic`, or `build:midi`.

**Distribution.** `build-all.ps1` builds every package the build computer can
produce and places them in `dist/`, where the `GET YOUR OWN STATION` page
offers them.

**Further references.**

| Document | Subject |
|---|---|
| `README.md` | Configuration, services, and networking |
| `DISTRIBUTION.md` | Building and installing packages |
| `BROADCASTING.md` | Radio programmes, automation, and interfaces |
| `stationnet/application_packages/APIREADME.md` | Program packages and runtime |
| `MUD_CREATOR_HANDBOOK.md` | Complete guide to MUD world creation |
| `stationnet/mud_world_templates/README_CUSTOM_WORLDS.md` | World file format |
| `stationnet/theme_templates/THEMES_README.md` | Colour theme format |

<!-- pagebreak -->

# Appendices

## Appendix A. Keyboard Reference

**Throughout the station**

| Key | Action |
|---|---|
| / | Move to the navigation bar (MUD: the command line) |
| Esc | Release focus; leave immersive program view |
| Tab | Move between controls |

**Chat**

| Key | Action |
|---|---|
| Enter | Send message |
| Shift+Enter | New line |

**Radio**

| Key | Action |
|---|---|
| V (hold) | Talk on a voice frequency |
| Space (hold) | Morse key, after BEGIN CW TRANSMISSION |
| Enter | Tune the frequency typed |

**Television**: see Section 14.3.

**Library (PDF)**

| Key | Action |
|---|---|
| ← → | Previous and next page |

**Program Manager**

| Key | Action |
|---|---|
| ↑ ↓ | Select program |
| Enter | Open program |

**MUD**

| Key | Action |
|---|---|
| Enter | Send speech or command |
| Tab | Accept suggestion |
| ↑ ↓ | Recall earlier commands |
| Esc | Close suggestions |

**Presentations (while presenting)**

| Key | Action |
|---|---|
| → , Page Down, Space | Next slide |
| ← | Previous slide |
| Esc | End presentation |

## Appendix B. Quick Reference

| Task | Procedure |
|---|---|
| Sign in | LOGIN; enter username and password |
| Sign out | STATION; [ DISCONNECT ] |
| Change password | [ PROFILE ]; [ EDIT PROFILE ]; Change password |
| Change colours | [ EDIT PROFILE ]; Theme preferences |
| Send mail | MAIL; COMPOSE + |
| Chat privately | USERS; DIRECT CHAT (member must be online) |
| Post a bulletin | BULLETIN BOARD; NEW BULLETIN + |
| Add a poll | NEW BULLETIN +; ADVANCED; Add a poll |
| Share a file | FILES; UPLOAD FILE + |
| RSVP to an event | EVENTS; choose event; GOING or INTERESTED |
| Report content | Open the item's `▾` menu, choose `REPORT`; give a reason |
| Listen to radio | POWER: OFF; SCAN > |
| Watch television | TV; POWER; GUIDE |
| Bookmark a book | While reading, BOOKMARK |
| Search the WIKI | WIKI; select collection; SEARCH |
| Pin a program | PROGRAMS; choose program; PIN TO FAVORITES |
| Start in the MUD | MUD; CREATE CHARACTER; /help |
| Unlock Office | Enter OFFICE Password |
| Export a document | Documents; EXPORT; choose format |
| Run BASIC | BASIC; RUN ▶ |

## Appendix C. Troubleshooting

**The station address does not open.**
The station may be stopped or outside its hours, the address or port may be
wrong, or the host's firewall may be blocking connections. Confirm the address
with the SYSOP. SYSOPs: check the station's status in the Station Manager and
permit the port in the firewall.

**"Invalid username or password."**
Usernames and passwords are case-sensitive. Use `FORGOT PASSWORD` as described
in Section 2.5.

**"Account suspended" or "Account banned."**
A SYSOP has restricted the account. The message shows the reason and any
expiry time.

**A service described in this manual is missing.**
The station does not offer it, or it requires you to sign in.

**The radio or a video is silent.**
Click anywhere on the page; browsers block sound until you do. Check `MUTE`,
the volume, and your device's volume.

**Live voice reports that the microphone is unavailable.**
Microphones require a secure connection. Use the station's `https://` address,
if it has one, or use the host computer itself. Allow microphone access when
the browser asks.

**"Your chat is muted."**
A SYSOP has muted your chat. You may still read.

**A direct chat has disappeared.**
Direct chats are erased when either member disconnects. This is normal.

**Office will not unlock.**
The OFFICE Password is different from your StationNet password. If it has been
lost, saved Office files cannot be recovered.

**The browser offers no "Install" command.**
Installation requires a secure address. See Chapter 30.

**A PSP game will not start.**
PSP games require a secure connection or the host computer, and WebGL 2.

**WIKI reports LIBRARY UNAVAILABLE.**
SYSOPs: place `.zim` files in the folder shown in the WIKI settings section.

**The Station Manager shows CRASHED.**
Read the last log line shown, open `Logs`, and correct the cause, such as a
port already in use. Then start the station again.

**A station will not start: port in use.**
Another station or program is using the port. Choose a different port in
`Settings`.

## Appendix D. Modules and Permissions

The following table lists each module, its condition when a station is first
set up, and who may use it.

| Module (tab) | Initially | Guests | Members | Reserved to SYSOP or delegates |
|---|---|---|---|---|
| BULLETIN BOARD | On | Read, if permitted | Read, post, reply | Remove any post |
| ANNOUNCEMENTS | On | Read | Read, vote | Post and edit |
| CHAT | On | — | Station and direct chat | Delete messages, mute |
| MAIL | On | — | Send and receive | — |
| RADIO | On | — | Listen; transmit by band rules | Host broadcasts (group) |
| FILES | On | — | Download; upload if permitted | Upload policy |
| USERS | On | — | Directory, profiles, feeds | Moderation |
| PROGRAMS | On | — | Run programs | Install and manage |
| IMAGEBOARD | Off | — | Post and reply | Pin, lock, delete (group) |
| EVENTS | Off | Read, if permitted | RSVP; create if permitted | Creation policy |
| WIKI | Off | Read, if permitted | Read | Install collections |
| VIDEOS | Off | Watch, if permitted | Watch; upload in Community mode | Curated uploads |
| LIBRARY | Off | Read, if permitted | Read; upload in Community mode | Categories, curated uploads |
| ARCADE | Off | Browse, if permitted | Play; upload in Community mode | Cores, curated uploads |
| TV | Off | Watch, if permitted | Watch | Channels and schedules |
| OFFICE | Off | — | Private use | — |
| MUD | Off | — | Play enabled worlds | Worlds; building (delegated) |
| USER FEEDS | On | — | Post, comment, react | Remove posts |

## Appendix E. Glossary

**Announcement.** An official notice posted by a SYSOP.

**Band.** A range of radio frequencies set aside for a particular purpose.

**Bulletin.** A public message on the bulletin board, with its replies.

**Callsign.** A random identifying code assigned to each station and each member.

**Collection.** One reference work in the WIKI.

**Core.** An emulator component that runs games for one platform in the Arcade.

**CW.** Continuous wave; Morse code transmission on the radio.

**Direct chat.** A private, unrecorded conversation between two online members.

**Guest.** A visitor who has not signed in.

**Host computer.** The computer on which a station runs.

**Imageboard.** A board of picture threads.

**Member.** A visitor who has signed in to an account.

**Module.** A component that provides one service and can be turned on or off.

**MUD.** Multi-user dungeon; a text-based multiplayer game.

**NPC.** A character in a MUD world controlled by the game.

**OFFICE Password.** The password that encrypts a member's Office files.

**Programme.** A scheduled item on television, or a sequence of content on the radio.

**PTT.** Push-to-talk; holding a control to transmit by voice.

**SSTV.** Slow-scan television; still-picture transmission on the radio.

**Station.** One running StationNet service, with its own name, accounts, and data.

**Station Manager.** The desktop program used to create, start, and stop stations.

**SYSOP.** System operator; the administrator of a station.

**Tagline.** A short line of text displayed beneath the station's name.

**World.** One game within the MUD, with its own characters.

**ZIM file.** The file format of WIKI reference collections.
