// Runs the old childcare calculator's own code (reference/childcare-app/public/index.html)
// on its saved state.json and on variations that reach every branch, and writes what it
// computes to reference/childcare-app/parity.json. The Python port is tested against that
// file (backend/tests/test_childcare_parity.py), so parity is with the old code itself,
// not with a reading of it.
//
//   node scripts/childcare-golden.mjs
//
// The output stays in reference/ beside the saved state it was computed from.

import { readFileSync, writeFileSync } from "node:fs";
import vm from "node:vm";

const root = new URL("../reference/childcare-app/", import.meta.url);
const html = readFileSync(new URL("public/index.html", root), "utf8");
const state = JSON.parse(readFileSync(new URL("state.json", root), "utf8"));

const open = html.indexOf("<script>") + "<script>".length;
const script = html.slice(open, html.indexOf("</script>", open));
// From the tax bands to the end of scenario(): the calculation, none of the rendering.
const calc = script.slice(script.indexOf("const TAX_BANDS"), script.indexOf("function weekDots"));

const context = { fields: {}, children: [] };
context.document = {
  getElementById: (id) => {
    const v = context.fields[id];
    return { value: v === true || v === false ? "" : String(v ?? ""), checked: v === true };
  },
};
vm.createContext(context);
vm.runInContext(calc, context);

const clone = (x) => JSON.parse(JSON.stringify(x));

function run(name, children, patterns, nurseries, fields) {
  context.children = children;
  context.fields = fields;
  context.patterns = patterns;
  context.nurseries = nurseries;
  const results = vm.runInContext(
    `nurseries.flatMap((n, ni) => patterns.map((p, pi) => {
       const s = scenario(n, p);
       return {
         nursery: ni, pattern: pi,
         gross: s.e.gross, pension_rate: s.e.pRate, pension: s.e.pension, tax: s.e.tax,
         tax_split: s.e.taxSplit, ni: s.e.ni, net: s.e.net,
         days: s.n.days, attended: s.n.dc.attended, leave: s.n.dc.leave, bank: s.n.dc.bank,
         cost: s.n.cost, tfc: s.n.tfc, childcare_net: s.n.net,
         lines: s.n.lines.map(l => ({ eff: l.eff, per_day: l.perDay, applied: l.applied, cost: l.cost, tfc: l.tfc })),
         cb_gross: s.cb.gross, cb_net: s.cb.net, clawback: s.cb.clawback,
         annual: s.annual, monthly: s.monthly,
       };
     }))`,
    context,
  );
  const allowance = vm.runInContext("personalAllowance()", context);
  const payslip = vm.runInContext('earnings(num("i-refhours")).net / 12', context);
  return { name, inputs: { children, patterns, nurseries, fields }, personal_allowance: allowance, payslip_monthly: payslip, results };
}

const base = state.fields;
const cases = [
  run("saved state", state.children, state.patterns, state.nurseries, base),
  run("no net pay arrangement", state.children, state.patterns, state.nurseries, { ...base, "i-npa": false }),
  run("partner part clawed back", state.children, state.patterns, state.nurseries, { ...base, "i-partner": "70000" }),
  run("partner below the charge", state.children, state.patterns, state.nurseries, { ...base, "i-partner": "50000" }),
  run("no child benefit", state.children, state.patterns, state.nurseries, { ...base, "i-usecb": false }),
  run("allowance tapered", state.children, state.patterns, state.nurseries, { ...base, "i-salary": "130000" }),
  run("default tax code", state.children, state.patterns, state.nurseries, { ...base, "i-taxcode": "" }),
  run(
    "hourly nursery, no TFC, three children",
    ["One", "Two", "Three"],
    [...clone(state.patterns), { days: 2.5, hours: 20 }],
    [
      { name: "Hourly", basis: "hour", tfc: false, billsLeave: false,
        rates: [{ rate: 8.5, disc: 0, min: 0 }, { rate: 9, disc: 5, min: 3 }, { rate: 9, disc: 10, min: 5 }] },
      { name: "Daily", basis: "day", tfc: true, billsLeave: true,
        rates: [{ rate: 95, disc: 0, min: 0 }, { rate: 95, disc: 15, min: 2 }, { rate: 95, disc: 15, min: 2 }] },
    ],
    base,
  ),
];

writeFileSync(new URL("parity.json", root), JSON.stringify({ generated_by: "scripts/childcare-golden.mjs", cases }, null, 1) + "\n");
console.log(`wrote ${cases.length} cases, ${cases.reduce((n, c) => n + c.results.length, 0)} scenarios`);
