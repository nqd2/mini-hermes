# WeAI Frontend UI Guidelines

These guidelines govern UI components, pages, settings tabs, and modals in `packages/frontend`.

## File Type Icons

- Always render file type icons using [`MaterialFileIcon.svelte`](../../packages/frontend/src/lib/components/common/MaterialFileIcon.svelte) (backed by [`materialFileIcon.ts`](../../packages/frontend/src/lib/components/common/materialFileIcon.ts) and static SVGs in `/material-icons/*.svg`).
- **Never** invent colored text extension pills (`DOCX`, `XLSX`, `PPTX`, `PDF`, `FILE`) as substitutes for file icons.
- If a file extension is missing from `EXTENSION_TO_ICON` in `materialFileIcon.ts`, add the mapping (and SVG asset if needed) there and cover it in `materialFileIcon.test.ts` rather than creating one-off badge helpers.
- Standard container styling:
  - List / compact rows: `<div class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-gray-100/80 dark:bg-white/[0.06]"><MaterialFileIcon filename={...} className="size-5" /></div>` (or bare `size-4` inline).
  - Detail headers / cards: `<div class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gray-100/80 dark:bg-white/[0.08]"><MaterialFileIcon filename={...} className="size-6" /></div>`.

## Borders, Surfaces, and Dark/Light Tokens

- Never use bare `border` / `border-b` / `border-t` without explicit light and dark color tokens; bare Tailwind borders render overly bright in dark mode.
- Preferred subtle surface and border tokens:
  - Card / container border: `border-gray-200/80 dark:border-white/[0.08]`
  - Card / subtle panel fill: `bg-white/70 dark:bg-white/[0.02]` or `bg-gray-50/70 dark:bg-white/[0.02]`
  - Inputs: `border-gray-200 bg-white dark:border-white/[0.08] dark:bg-white/[0.03]` with `focus:border-blue-500 dark:focus:border-blue-400`
  - Empty / dashed placeholders: `border-dashed border-gray-200 dark:border-white/[0.1]`

## Button Hierarchy and Affordance

- **Primary CTA** (1 main action per toolbar/form): `bg-gray-900 text-white hover:bg-black dark:bg-white dark:text-gray-950 dark:hover:bg-gray-200` (with icon where helpful).
- **Secondary action** (including Settings action buttons like Import, Export, Manage, Add, See what's new, Reconnect): use a subtle bordered interactive button (`border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 dark:border-white/[0.1] dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08]`) with a clear label — avoid faint borderless text buttons or unlabeled tiny `+` icons for primary add actions.
- **Destructive action** (Danger Zone Reset, Delete All, Remove): use semantic rose/red bordered styling (`border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20`).

## Contrast, Settings Structure, and Feedback

- Use `UserSettingSection` / `UserSettingRow` / `UserSettingField` for Personal Settings tabs and `AdminSettingSection` / `AdminSettingRow` / `AdminSettingField` for Admin Settings tabs.
- Maintain readable dark-mode contrast for secondary descriptions and section headers (`text-gray-500 dark:text-gray-400`; avoid low-contrast `dark:text-gray-600` on dark backgrounds).
- Use `svelte-sonner` toasts (`toast.success` / `toast.error` / `toast.info`) for action feedback; pair with semantic alert banners when persistent visibility is required.
- Avoid showing raw `JSON.stringify(...)` payloads to end users by default; present human-readable summaries and place raw technical payloads behind an expandable disclosure (`<details>`) if needed.
- Keep user-facing UI branded as **WeAI**; do not add external Open WebUI community/social/docs links outside minimal open-source license attribution in Settings → About (`Credits`).
