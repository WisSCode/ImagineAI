"""Unidades puras del pipeline: parser, ensamblado react y saneado de JSX."""
from backend import pipeline


def test_parse_files_markers():
    raw = (
        "<<<FILE: index.html>>>\n<!DOCTYPE html><html></html>\n<<<END>>>\n"
        "<<<FILE: styles.css>>>\nbody { margin: 0; }\n<<<END>>>"
    )
    files = pipeline.parse_files(raw)
    assert set(files) == {"index.html", "styles.css"}
    assert "margin: 0" in files["styles.css"]


def test_parse_files_tolerates_deformed_markers_and_paths():
    raw = "<< FILE: src/app.js >>\nconsole.log(1);\n<<END>>"
    files = pipeline.parse_files(raw)
    assert list(files) == ["app.js"]  # la ruta se neutraliza al nombre base


def test_parse_files_fence_fallback():
    raw = "Aquí está:\n```css\nh1 { color: red; }\n```"
    assert "styles.css" in pipeline.parse_files(raw)


def test_react_shell_roundtrip():
    files = {
        "index.html": (
            "<!DOCTYPE html><html><head>"
            '<link rel="stylesheet" href="styles.css" /></head>'
            '<body><div id="root"></div>'
            '<script type="text/babel" src="app.jsx" data-presets="react"></script>'
            "</body></html>"
        ),
        "styles.css": ".reveal { opacity: 0; }",
        "app.jsx": "function App() { return <h1>Hola</h1>; }",
    }
    assembled = pipeline.assemble_react_index(files)
    assert ".reveal" in assembled and "function App" in assembled
    # El cascarón extraído no debe contener ya el CSS/JSX embebidos…
    shell = pipeline.extract_react_shell(assembled)
    assert ".reveal" not in shell and "function App" not in shell
    # …y reensamblar con contenidos nuevos los inserta en su sitio.
    final = pipeline.reassemble_react_index(shell, ".nuevo { color: red; }",
                                            "function App() { return <h2>Editado</h2>; }")
    assert ".nuevo" in final and "Editado" in final


def test_neutralize_jsx_images():
    jsx = '<img className="w-8 h-8" src="foto.png" alt="Retrato" />'
    out, n = pipeline.neutralize_jsx_images(jsx)
    assert n == 1
    assert "<img" not in out
    assert "w-8 h-8" in out and "Retrato" in out


def test_unblock_reveal_gates():
    jsx = (
        "function Hero() {\n"
        "  const [heroRef, isHeroVisible] = useReveal(0.45);\n"
        "  if (!isHeroVisible) return null;\n"
        "  return <div ref={heroRef}>Hola</div>;\n"
        "}\n"
        "function Menu() {\n"
        "  const [ref, shown] = useReveal();\n"
        "  if (!shown) { return null; }\n"
        "  return <section ref={ref}>Menú</section>;\n"
        "}\n"
    )
    out, n = pipeline.unblock_reveal_gates(jsx)
    assert n == 2
    assert "return null" not in out
    assert "Hola" in out and "Menú" in out


def test_unblock_reveal_gates_ignores_unrelated_nulls():
    jsx = (
        "function Modal({ open }) {\n"
        "  if (!open) return null;\n"
        "  return <div>modal</div>;\n"
        "}\n"
    )
    out, n = pipeline.unblock_reveal_gates(jsx)
    assert n == 0
    assert out == jsx


def test_sanitize_jsx_strips_modules():
    jsx = 'import React from "react";\nexport default function App() { return null; }'
    out = pipeline._sanitize_jsx(jsx)
    assert "import" not in out and "export" not in out and "function App" in out
