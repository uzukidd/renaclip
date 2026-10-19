/* Offline MathJax 3.2.2 SVG renderer; see README.md and LICENSE.mathjax. */
import {mathjax} from 'mathjax-full/js/mathjax.js';
import {TeX} from 'mathjax-full/js/input/tex.js';
import {SVG} from 'mathjax-full/js/output/svg.js';
import {liteAdaptor} from 'mathjax-full/js/adaptors/liteAdaptor.js';
import {RegisterHTMLHandler} from 'mathjax-full/js/handlers/html.js';
import {AllPackages} from 'mathjax-full/js/input/tex/AllPackages.js';

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
// Upstream default: const input = new TeX({packages: AllPackages});
// Disable host-facing HTML and dynamic package commands in this offline adapter.
const input = new TeX({
  packages: AllPackages.filter(name => !['require', 'autoload', 'html'].concat('noundefined').includes(name)),
});
const output = new SVG({fontCache: 'none'});
const document = mathjax.document('', {InputJax: input, OutputJax: output});

/**
 * Convert TeX (without $ delimiters) to a JSON string containing standalone SVG.
 * display defaults to false. Sizes retain MathJax's ex units; viewBox is unitless.
 * Invalid TeX follows MathJax's default behavior (an error SVG).
 */
// Browser-style assignment: globalThis.renderFormula = function renderFormula(tex, display = false) {
// QJSEngine does not expose globalThis; obtain its ECMAScript global object.
const runtimeGlobal = Function('return this')();
runtimeGlobal.renderFormula = function renderFormula(tex, display = false) {
  if (typeof tex !== 'string') {
    throw new TypeError('renderFormula: tex must be a string');
  }
  // Avoid cross-call label/number state; fontCache:none keeps glyph paths local.
  input.reset();
  const container = document.convert(tex, {
    display: Boolean(display),
    em: 16,
    ex: 8,
    containerWidth: 80 * 16,
  });
  const svg = adaptor.firstChild(container);
  const result = {
    svg: adaptor.outerHTML(svg),
    width: adaptor.getAttribute(svg, 'width'),
    height: adaptor.getAttribute(svg, 'height'),
  };
  const verticalAlign = adaptor.getStyle(svg, 'vertical-align');
  if (verticalAlign) result.verticalAlign = verticalAlign;
  return JSON.stringify(result);
};
