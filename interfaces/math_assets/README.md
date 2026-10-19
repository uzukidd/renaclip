# Offline MathJax SVG renderer

This directory provides a self-contained JavaScript bundle for PySide6.QtQml.QJSEngine. No Node.js, browser DOM, WebEngine, network connection, external font files, or additional Python packages are needed at runtime. Node.js/npm are used only to rebuild it.

## Files and provenance

- `renderer.js`: local renderer entry point, importing MathJax's `liteAdaptor`, `TeX`, `SVG`, and `AllPackages`.
- `renderer.bundle.js`: esbuild IIFE bundle, including the TeX parser, SVG output, and glyph paths.
- `LICENSE.mathjax`: unmodified license from the `mathjax-full@3.2.2` npm package.
- `LICENSE.mhchemparser`: unmodified license for the chemistry parser included through `AllPackages`.

**License correction:** upstream `mathjax-full@3.2.2` is **Apache-2.0**, not MIT. Its bundled license and package metadata are authoritative. `mhchemparser@4.2.1` is also Apache-2.0. esbuild is MIT licensed and is a build tool, not a runtime dependency.

Upstream sources: https://www.npmjs.com/package/mathjax-full/v/3.2.2 and https://github.com/mathjax/MathJax-src/tree/3.2.2 . Build tool: `esbuild@0.25.12`. Upstream sources are not patched; this bundle compiles them together with the local entry point. esbuild retains upstream legal comments. All npm packages, npm manifests, lockfiles, and intermediate build files live in a separate temporary directory.

## JavaScript API

Evaluate `renderer.bundle.js` once in a QJSEngine, then call:

```javascript
renderFormula('\\frac{x^2 + 1}{y}', true)
renderFormula('E = mc^2', false)
renderFormula('\\begin{bmatrix}1 & 2 \\\\ 3 & 4\\end{bmatrix}', true)
```

The global function `renderFormula(tex, display = false)` returns a **JSON string**:

```json
{"svg":"<svg xmlns=\"http://www.w3.org/2000/svg\" ...>...</svg>","width":"...ex","height":"...ex","verticalAlign":"...ex"}
```

`tex` is a string without enclosing `$`, `$$`, `\\(`, or `\\[` delimiters. `display` selects display or inline layout. `width` and `height` preserve MathJax's SVG units, normally `ex`; the SVG also contains a unitless `viewBox`. `verticalAlign` is included when MathJax supplies it. Rendering uses fixed layout metrics `em=16`, `ex=8`, and `containerWidth=1280` and does not query any host DOM. TeX numbering/label state is reset for each call.

The SVG uses `fontCache: 'none'`: normal math glyphs are local `<path>` elements, with no external glyph/font cache. To set a color downstream, replace `currentColor` in the returned SVG with an application-selected CSS color, for example `svg.replace('currentColor', '#e8e8e8')` in Python. Explicit TeX color commands retain their own colors. Arbitrary text or unsupported Unicode may use SVG text fallback, whose appearance depends on the downstream SVG renderer's available fonts; the renderer does not fetch fonts. A downstream SVG viewer may need to interpret `ex` dimensions or map the `viewBox` to a desired output size.

Non-string input throws a TypeError. TeX errors normally produce MathJax's error SVG; callers can check for `data-mjx-error`. `AllPackages` means MathJax 3.2.2's statically imported package list; dynamic loading (`\\require`, autoloading arbitrary packages), browser interaction, and URL fetching are not provided. Calls run synchronously on the QJSEngine's owning thread.

The enabled package list is `AllPackages` filtered to exclude `html`, `require`, and `autoload`, plus `noundefined`. The latter two are absent from the upstream static list already; the explicit filter records this offline restriction. HTML commands that could attach URLs or host markup are disabled. The bundle registers its function through `Function('return this')()` because this QJSEngine does not expose `globalThis`; this uses only standard JavaScript.

Example host call after creating a Qt application:

```python
import json
from pathlib import Path
from PySide6.QtQml import QJSEngine

engine = QJSEngine()
assets = Path('interfaces/math_assets')
loaded = engine.evaluate((assets / 'renderer.bundle.js').read_text(encoding='utf-8'))
if loaded.isError():
    raise RuntimeError(loaded.toString())
tex = r'\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}'
value = engine.evaluate('renderFormula(' + json.dumps(tex) + ', true)')
if value.isError():
    raise RuntimeError(value.toString())
result = json.loads(value.toString())
svg = result['svg'].replace('currentColor', '#202020')
```

## Rebuild outside the repository

These are documentation commands, not installed scripts. Run from the repository root with Node.js/npm installed. Both variants stage the source and install packages in the OS temporary directory, then write only the final bundle and license copies into this directory. Build versions are pinned; the exact transitive dependency used by the bundle is also pinned. Keep the generated temporary npm lockfile if reproducing the complete installation environment later.

Windows PowerShell:

```powershell
$assets = Join-Path (Get-Location) 'interfaces/math_assets'
$build = Join-Path $env:TEMP ('renaclip-mathjax-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $build | Out-Null
Copy-Item (Join-Path $assets 'renderer.js') (Join-Path $build 'renderer.js')
Push-Location $build
try {
    npm.cmd install --no-audit --no-fund --save-exact mathjax-full@3.2.2 mhchemparser@4.2.1 esbuild@0.25.12
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
    .\node_modules\.bin\esbuild.cmd renderer.js --bundle --format=iife --platform=neutral --target=es2015 --minify --legal-comments=inline "--outfile=$assets/renderer.bundle.js"
    if ($LASTEXITCODE -ne 0) { throw 'esbuild failed' }
    Copy-Item node_modules/mathjax-full/LICENSE (Join-Path $assets 'LICENSE.mathjax')
    Copy-Item node_modules/mhchemparser/LICENSE.txt (Join-Path $assets 'LICENSE.mhchemparser')
} finally {
    Pop-Location
}
```

POSIX shell (Linux/macOS):

```sh
assets="$(pwd)/interfaces/math_assets"
build="$(mktemp -d "${TMPDIR:-/tmp}/renaclip-mathjax.XXXXXXXX")"
cp "$assets/renderer.js" "$build/renderer.js"
(
    set -e
    cd "$build"
    npm install --no-audit --no-fund --save-exact mathjax-full@3.2.2 mhchemparser@4.2.1 esbuild@0.25.12
    ./node_modules/.bin/esbuild renderer.js --bundle --format=iife --platform=neutral --target=es2015 --minify --legal-comments=inline --outfile="$assets/renderer.bundle.js"
    cp node_modules/mathjax-full/LICENSE "$assets/LICENSE.mathjax"
    cp node_modules/mhchemparser/LICENSE.txt "$assets/LICENSE.mhchemparser"
)
```

## Verification

The bundle is smoke tested using the existing project interpreter `D:\miniconda3\envs\renaclip\python.exe` with PySide6.QtQml.QJSEngine and no WebEngine. Tests evaluate the bundle without `window`, `process`, or `require`, render a bmatrix, a fraction, inline math, Greek symbols, and an AMS aligned expression, and verify parsable standalone SVG, dimensions/viewBox, local glyph paths, color placeholders, no external references, repeat-call stability, and argument validation.

Verified with Qt 6.12.0: all six render cases and TypeError validation passed. Example results: bmatrix `6.914ex` × `5.43ex`, fraction `7.174ex` × `5.724ex`, inline `8.699ex` × `2.072ex`. Qt 6.12 emits `qt.qml.usedbeforedeclared` diagnostics about upstream hoisted variables during bundle evaluation; these are warnings, and evaluation returns without a JavaScript error. Older Qt versions have not been tested.

Bundle size: 1,820,260 bytes. SHA-256 (final build after disabling `noundefined`):

```text
renderer.js         09DF4C20A52DA833DF1965FC5965D955AACDEBD4B0B33296F641A6C072173CFB
renderer.bundle.js  7E19C50FAB231E19A5E6E54F2852FE9157C4BB52002AB312064120515A35DF66
```

<!-- Superseded smoke-build provenance retained for edit history:
Bundle size: 1,820,238 bytes. SHA-256:

```text
renderer.js         C65D90DD7FE259304DC05A2709E7A049DFC337BDA10CE64807CC2D109FF4DC20
renderer.bundle.js  446CEFAD12A26A39E1A57474AD6AC93A910D7554C478DAEE1E51759C94A7DDA2
```
-->

Build environment used Node.js 24.21.0 and npm 11.19.0. npm installed some unused dependencies of `mathjax-full`, including browser/speech packages; those packages are not imported by this renderer and are not required by the resulting bundle. On npm versions that require explicit install-script approval, esbuild's optional platform binary package is sufficient for the CLI used above.
