// The dropdown on every page of apps.kblab.me. Each gallery loads this with one script tag,
// so adding an app is one line here. A page that cannot load it just has no dropdown.
const APPS = [
  { name: "Playground", href: "/playground/" },
  { name: "DodginBalls", href: "/dodginballs/" },
  { name: "Apociseum", href: "/apociseum/" },
  { name: "Shape City", href: "/shape-city/" },
  { name: "PlaceMyParents", href: "/" },
  { name: "Game admin", href: "https://games.kblab.me/" },
];

const path = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
// Longest prefix wins, since "/" matches every path.
const current = APPS
  .filter((app) => app.href.startsWith("/") && path.startsWith(app.href))
  .sort((a, b) => b.href.length - a.href.length)[0];

const select = document.createElement("select");
select.style.cssText = "font:inherit;color:inherit;background:transparent;max-width:100%";
for (const app of APPS) {
  const option = document.createElement("option");
  option.value = app.href;
  option.textContent = app.name;
  option.selected = app === current;
  select.append(option);
}
select.addEventListener("change", () => location.assign(select.value));

const label = document.createElement("label");
label.style.cssText = "display:block;padding:8px 16px;font:14px/1.5 system-ui,sans-serif";
label.append("App: ", select);
document.body.prepend(label);
