# StationNet colour themes

Use [template-theme.json](template-theme.json) as a starting point. Its `_comment_*`
fields explain what each colour controls. They are valid JSON and are discarded
when the theme is imported, so you can leave them in your edited copy.

## Create and install a theme

1. Copy `template-theme.json` to a new file, such as `ocean-night.json`.
   StationNet copies the template to `<STATIONNET_DATA>/themes/` when it first
   creates that directory. The copy in this repository has the latest guidance.
2. Set `id` to a unique ID for that station, using lowercase letters, digits,
   and hyphens (up to 64 characters). Set `name` and `author` to nonempty text
   (up to 80 characters each). The file name must be `<id>.json` if you install
   it directly in the themes directory.
3. Edit the colours you want. Each value must be a six-digit hex colour such
   as `#00d8cf`. Keep **every colour key** in any section you include. You may
   remove the entire `site` or `masthead` section to make a theme for only one
   of the two selectors.
4. In **SYSOP CONTROL → Station settings**, import the edited JSON file. You
   can also place it in `<STATIONNET_DATA>/themes/` as `<id>.json` and refresh
   Station settings. The template itself is never listed as a selectable theme.

The `site` section sets the station interface colours. The `masthead` section
sets the large text logo and its shadow. These are selected independently in
Station settings and in each member's theme preferences. A theme containing
only one section appears only in that section's selector. Imported themes are
available to all members of that station.

## What the colour keys mean

The key names describe the original palette; you can assign any suitable hex
colour to them. The descriptions are examples of where each colour appears,
not a promise that every element of that type uses it.

| Key | Typical use |
| --- | --- |
| `green` | Main text, navigation links, primary borders |
| `green-bright` | Headings, emphasized text, form control text |
| `green-dim` | Subtle borders, dividers, heading shadows, scrollbar thumbs |
| `cyan` | Links, focus outlines, accent labels and borders |
| `magenta` | Hovered links, secondary accents |
| `amber` | Warnings, badges, highlighted details |
| `red` | Error and danger text, status indicators |
| `red-dim` | Muted danger borders and shadows |
| `grey` | Muted text, captions, metadata |
| `white` | Secondary body text and text on emphasized surfaces |
| `black` | Main page and dark panel background, scrollbar tracks |
| `field` | Input, textarea, and select backgrounds |
| `button` | Button backgrounds |
| `masthead` | Large text logo colour |
| `masthead-shadow` | Offset shadow behind the logo |

`green` and `cyan` also drive translucent accents and the dark panel, hover,
selection, and soft-border surfaces, which StationNet derives automatically as
dark tints of those colours. If `field` or `button` keep the template's default
dark greens (`#030703` and `#001b08`) while `green` is changed, they are tinted
from `green` too; any other value is used exactly. Special status styles
(warnings, errors) and document content keep fixed colours, so a custom theme
does not recolour every pixel. Choose colours with enough contrast for
text, controls, and the masthead shadow, then preview both selectors in Station
settings.
