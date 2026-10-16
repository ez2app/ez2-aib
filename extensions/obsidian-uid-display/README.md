# Ez2 UID Display (Obsidian plugin)

A uid-layout vault stores folders as `fld_…` and notes as `<uid>.md`. This plugin changes only what Obsidian shows:

| Where | Shows |
|---|---|
| File explorer, folders | `folders[<folder id>].title` from `<vault>/.ez2/display/labels.json` |
| File explorer, notes | the note's frontmatter `title`, or the file name when there is none |
| Tab headers | the note's frontmatter `title`, or the file name |
| File explorer order | sorted by the shown titles (numbers in natural order, folders first) when the explorer sorts by name; the by-time orders are left as they are |

- It never renames, moves or writes a file. Sync and `[[wikilinks]]` keep working on the uid names.
- The vault root comes from Obsidian (`app.vault.adapter.getBasePath()`), so moving the vault needs no change.
- `labels.json` is re-read when it changes. When it is missing or broken, folders show their ids and nothing fails.
- The view header and the inline title still show the file name: they can be edited, and editing them would rename the file.
- `labels.json` also holds names for zh-TW / en / ja / ko. The plugin uses `title` for now; the language switch is reserved.
- Sorting wraps the explorer's own `getSortedFolderItems`, which is not public API. On an Obsidian without it the explorer keeps its own order and the titles still show.
- Search and the quick switcher still match file names. Note titles are also in each note's `aliases`, so the quick switcher finds them.

## Install

1. Back up `<vault>/.obsidian/community-plugins.json`.
2. Copy `manifest.json` and `main.js` to `<vault>/.obsidian/plugins/ez2-uid-display/`.
3. In Obsidian: Settings → Community plugins → reload, then turn on **Ez2 UID Display**.

Turning the plugin off puts every name back the way Obsidian shows it.
