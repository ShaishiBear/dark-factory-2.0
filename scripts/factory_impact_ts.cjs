#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");
const requireHere = createRequire(path.join(process.cwd(), "package.json"));
const ts = requireHere("typescript");

const repo = path.resolve(process.cwd(), "../..");
const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const wanted = new Set(input.files.map((p) => path.resolve(repo, p)));
const ranges = input.ranges || {};
const cfgPath = path.join(process.cwd(), "tsconfig.json");
const cfg = ts.readConfigFile(cfgPath, ts.sys.readFile);
if (cfg.error) throw new Error(ts.flattenDiagnosticMessageText(cfg.error.messageText, "\n"));
const parsed = ts.parseJsonConfigFileContent(cfg.config, ts.sys, process.cwd());
const options = parsed.options;
const files = parsed.fileNames;
const versions = new Map(files.map((f) => [f, "0"]));
const host = {
  getCompilationSettings: () => options,
  getScriptFileNames: () => files,
  getScriptVersion: (f) => versions.get(f) || "0",
  getScriptSnapshot: (f) => { try { return ts.ScriptSnapshot.fromString(fs.readFileSync(f, "utf8")); } catch { return undefined; } },
  getCurrentDirectory: () => process.cwd(),
  getDefaultLibFileName: (o) => ts.getDefaultLibFilePath(o),
  fileExists: ts.sys.fileExists, readFile: ts.sys.readFile, readDirectory: ts.sys.readDirectory,
};
const service = ts.createLanguageService(host, ts.createDocumentRegistry());
const program = service.getProgram();
if (!program) throw new Error("TypeScript program unavailable");

const rel = (f) => path.relative(repo, f).replaceAll(path.sep, "/");
const isTest = (f) => { const r = "/" + rel(f); return r.includes("/__tests__/") || r.includes(".test.") || r.includes(".spec."); };
const callers = new Set();
const tests = new Set();
const symbols = [];
const symbolSeen = new Set();
const changedNames = [];

function intersects(file, node) {
  const rs = ranges[rel(file.fileName)];
  if (!rs || !rs.length) return true;
  const a = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
  const b = file.getLineAndCharacterOfPosition(node.getEnd()).line + 1;
  return rs.some(([lo, hi]) => a <= hi && b >= lo);
}
function exported(node) {
  return !!node.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword || m.kind === ts.SyntaxKind.DefaultKeyword);
}
function names(node) {
  if (node.name && ts.isIdentifier(node.name)) return [node.name];
  if (ts.isVariableStatement(node)) return node.declarationList.declarations.flatMap((d) => ts.isIdentifier(d.name) ? [d.name] : []);
  return [];
}
function collect(sf, node, top = true) {
  const declaration = ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node) ||
    ts.isTypeAliasDeclaration(node) || ts.isEnumDeclaration(node) || ts.isVariableStatement(node) || ts.isMethodDeclaration(node);
  if (declaration && intersects(sf, node)) {
    for (const name of names(node)) {
      const key = `${rel(sf.fileName)}:${name.text}:${name.getStart(sf)}`;
      if (symbolSeen.has(key)) continue;
      symbolSeen.add(key);
      const line = sf.getLineAndCharacterOfPosition(name.getStart(sf)).line + 1;
      const item = { language: "typescript", file: rel(sf.fileName), name: name.text, line, public: top && exported(node) };
      symbols.push(item); changedNames.push([sf.fileName, name]);
    }
  }
  node.forEachChild((child) => collect(sf, child, false));
}

for (const sf of program.getSourceFiles()) {
  if (wanted.has(path.resolve(sf.fileName))) for (const stmt of sf.statements) collect(sf, stmt, true);
  for (const stmt of sf.statements) {
    if (!ts.isImportDeclaration(stmt) && !ts.isExportDeclaration(stmt)) continue;
    const spec = stmt.moduleSpecifier;
    if (!spec || !ts.isStringLiteral(spec)) continue;
    const resolved = ts.resolveModuleName(spec.text, sf.fileName, options, ts.sys).resolvedModule?.resolvedFileName;
    if (resolved && wanted.has(path.resolve(resolved))) (isTest(sf.fileName) ? tests : callers).add(rel(sf.fileName));
  }
}
for (const [file, name] of changedNames) {
  const groups = service.findReferences(file, name.getStart()) || [];
  for (const group of groups) for (const ref of group.references) {
    if (ref.isDefinition) continue;
    const r = rel(ref.fileName);
    if (wanted.has(path.resolve(ref.fileName))) continue;
    (isTest(ref.fileName) ? tests : callers).add(r);
  }
}
process.stdout.write(JSON.stringify({ symbols, callers: [...callers].sort(), tests: [...tests].sort() }));
