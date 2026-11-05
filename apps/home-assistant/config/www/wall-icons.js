// House icon set for what MDI has no icon for, so an entity can use `wall:<name>`
// wherever HA takes an icon (room_layout.yaml `icon`, the plug Settings editor).
// Loaded on every HA page via `frontend: extra_module_url`. Paths are 24x24, one
// color. A cut-out winds against its shape, since ha-icon fills with the nonzero rule.

const ICONS = {
  monkey:
    "M.4 11.5a2.8 2.8 0 1 1 5.6 0a2.8 2.8 0 1 1 -5.6 0Z" +
    "M18 11.5a2.8 2.8 0 1 1 5.6 0a2.8 2.8 0 1 1 -5.6 0Z" +
    "M3.5 11.5a8.5 8.5 0 1 1 17 0a8.5 8.5 0 1 1 -17 0Z" +
    "M12 7.2C10.8 5.9 7.2 5.9 6.6 9.4C6.3 11 6.9 12.4 7.6 13.2C6.8 15 8.2 19 12 19" +
    "C15.8 19 17.2 15 16.4 13.2C17.1 12.4 17.7 11 17.4 9.4C16.8 5.9 13.2 5.9 12 7.2Z" +
    "M8.4 10.3a1.2 1.2 0 1 1 2.4 0a1.2 1.2 0 1 1 -2.4 0Z" +
    "M13.2 10.3a1.2 1.2 0 1 1 2.4 0a1.2 1.2 0 1 1 -2.4 0Z" +
    "M10.8 13.6a.4 .4 0 1 1 .8 0a.4 .4 0 1 1 -.8 0Z" +
    "M12.4 13.6a.4 .4 0 1 1 .8 0a.4 .4 0 1 1 -.8 0Z" +
    "M9 15.4Q12 16.8 15 15.4Q12 18 9 15.4Z",
};

window.customIcons = window.customIcons || {};
window.customIcons.wall = {
  getIcon: async (name) => ({ path: ICONS[name] }),
  getIconList: async () => Object.keys(ICONS).map((name) => ({ name })),
};
