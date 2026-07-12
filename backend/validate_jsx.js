// Valida la SINTAXIS de un archivo .jsx transpilándolo con @babel/standalone.
// Uso: node validate_jsx.js <ruta_al_archivo>
// Imprime "OK" si compila, o "ERR: <mensaje>" si hay error de sintaxis.
// (Solo comprueba sintaxis, no referencias en tiempo de ejecución.)
const fs = require("fs");
const path = require("path");
try {
  const Babel = require(path.join(__dirname, "vendor", "babel.min.js"));
  const code = fs.readFileSync(process.argv[2], "utf8");
  Babel.transform(code, { presets: ["react"], filename: "app.jsx" });
  process.stdout.write("OK");
} catch (e) {
  const msg = (e && e.message ? e.message : String(e)).split("\n").slice(0, 6).join("\n");
  process.stdout.write("ERR: " + msg);
}
