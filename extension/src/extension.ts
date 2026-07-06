import * as vscode from "vscode";
import { spawn } from "child_process";

interface Finding {
  file: string;
  resource: string;
  resourceType: string;
  property: string;
  severity: "error" | "warning" | "info";
  message: string;
  worstCase: string;
  length: number | null;
  limit: number;
  margin: number | null;
  maxStackNameLen: number | null;
  unresolved: boolean;
  range: { startLine: number; startCol: number; endLine: number; endCol: number };
  removeRange: { startLine: number; startCol: number; endLine: number; endCol: number } | null;
  removable: boolean;
}

interface FileResult {
  file: string;
  isTemplate: boolean;
  parseError?: string;
  stackName?: string | null;
  budgetMode?: boolean;
  findings: Finding[];
}

let diagnostics: vscode.DiagnosticCollection;
let output: vscode.OutputChannel;
let statusBar: vscode.StatusBarItem;
const timers = new Map<string, NodeJS.Timeout>();
const findingsByUri = new Map<string, Finding[]>();
let cachedPython: string | null = null;
let coreVersion: string | null = null;

// ---------------------------------------------------------------- helpers
function cfg<T>(key: string, def: T): T {
  return vscode.workspace.getConfiguration("cfnNameCheck").get<T>(key, def);
}

function looksLikeTemplate(doc: vscode.TextDocument): boolean {
  if (!["yaml", "json"].includes(doc.languageId)) return false;
  const head = doc.getText(new vscode.Range(0, 0, Math.min(doc.lineCount, 400), 0));
  return /AWSTemplateFormatVersion|(^|\n)\s*Resources\s*:/.test(head) && /AWS::/.test(doc.getText());
}

async function resolvePython(): Promise<string | null> {
  const configured = cfg("pythonPath", "");
  const candidates = configured ? [configured] : ["python3", "python", "py"];
  for (const c of candidates) {
    const ver = await new Promise<string | null>((res) => {
      const p = spawn(c, ["-c", "import cfn_name_check; print(cfn_name_check.__version__)"]);
      let out = "";
      p.stdout.on("data", (d) => (out += d));
      p.on("error", () => res(null));
      p.on("exit", (code) => res(code === 0 ? out.trim() : null));
    });
    if (ver) {
      coreVersion = ver;
      return c;
    }
  }
  return null;
}

// ------------------------------------------------------------- core updates
function semverLess(a: string, b: string): boolean {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] ?? 0, y = pb[i] ?? 0;
    if (x !== y) return x < y;
  }
  return false;
}

async function latestCoreVersion(): Promise<{ version: string; installSpec: string } | null> {
  const mode = cfg("updateCheck", "off");
  try {
    if (mode === "pypi") {
      const pkg = cfg("updatePackage", "cfn-name-check");
      const r = await fetch(`https://pypi.org/pypi/${pkg}/json`);
      if (!r.ok) return null;
      const j: any = await r.json();
      return { version: j.info.version, installSpec: `${pkg}` };
    }
    if (mode === "github") {
      const repo = cfg("updateGithubRepo", "");
      if (!repo) return null;
      const r = await fetch(`https://api.github.com/repos/${repo}/releases/latest`, {
        headers: { "User-Agent": "cfn-name-check-vscode" },
      });
      if (!r.ok) return null;
      const j: any = await r.json();
      const version = String(j.tag_name || "").replace(/^v/, "");
      const wheel = (j.assets || []).find((a: any) => a.name.endsWith(".whl"));
      const installSpec = wheel
        ? wheel.browser_download_url
        : `cfn-name-check @ git+https://github.com/${repo}@${j.tag_name}#subdirectory=python`;
      return version ? { version, installSpec } : null;
    }
  } catch {
    /* offline / rate-limited: silently skip */
  }
  return null;
}

async function maybeCheckForCoreUpdate(state: vscode.Memento) {
  if (cfg("updateCheck", "off") === "off" || !cachedPython || !coreVersion) return;
  const intervalH = cfg("updateIntervalHours", 24);
  const last = state.get<number>("lastUpdateCheck", 0);
  if (Date.now() - last < intervalH * 3600 * 1000) return;
  await state.update("lastUpdateCheck", Date.now());
  const latest = await latestCoreVersion();
  if (!latest || !semverLess(coreVersion, latest.version)) return;
  if (state.get<string>("skippedVersion") === latest.version) return;
  const pick = await vscode.window.showInformationMessage(
    `cfn-name-check core ${latest.version} is available (installed: ${coreVersion}). ` +
      `Updating refreshes the AWS limits database — no extension update needed.`,
    "Update now", "Skip this version", "Later"
  );
  if (pick === "Skip this version") {
    await state.update("skippedVersion", latest.version);
    return;
  }
  if (pick !== "Update now") return;
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `Updating cfn-name-check core to ${latest.version}…` },
    () =>
      new Promise<void>((resolve) => {
        const p = spawn(cachedPython!, ["-m", "pip", "install", "--upgrade", latest.installSpec]);
        let err = "";
        p.stderr.on("data", (d) => (err += d));
        p.on("exit", (code) => {
          if (code === 0) {
            coreVersion = latest.version;
            vscode.window.showInformationMessage(`cfn-name-check core updated to ${latest.version}.`);
            vscode.workspace.textDocuments.forEach(scheduleCheck);
          } else {
            output.appendLine(`core update failed: ${err}`);
            vscode.window.showErrorMessage("cfn-name-check core update failed — see Output panel.");
          }
          resolve();
        });
        p.on("error", () => resolve());
      })
  );
}

function runCli(python: string, args: string[], stdin?: string): Promise<{ out: string; err: string }> {
  return new Promise((resolve, reject) => {
    const p = spawn(python, ["-m", "cfn_name_check.cli", ...args]);
    let out = "", err = "";
    p.stdout.on("data", (d) => (out += d));
    p.stderr.on("data", (d) => (err += d));
    p.on("error", reject);
    p.on("exit", () => resolve({ out, err }));
    if (stdin !== undefined) {
      p.stdin.write(stdin);
    }
    p.stdin.end();
  });
}

function buildArgs(filename: string, viaStdin: boolean): string[] {
  const args = viaStdin ? ["-", "--filename", filename] : [filename];
  args.push("--format", "json", "--fail-on", "never");
  const stackName = cfg("stackName", "");
  if (stackName) args.push("--stack-name", stackName);
  args.push("--description-regex", cfg("descriptionRegex", "StackName=([^\\s|,;]+)"));
  args.push("--warn-margin", String(cfg("warnMargin", 5)));
  if (cfg("strictMode", false)) args.push("--strict");
  return args;
}

function toDiagnostics(result: FileResult): vscode.Diagnostic[] {
  const showBudget = cfg("showBudgetHints", true);
  const diags: vscode.Diagnostic[] = [];
  for (const f of result.findings) {
    if (f.severity === "info" && !(result.budgetMode && showBudget && f.maxStackNameLen !== null)) continue;
    const sev =
      f.severity === "error"
        ? vscode.DiagnosticSeverity.Error
        : f.severity === "warning"
        ? vscode.DiagnosticSeverity.Warning
        : vscode.DiagnosticSeverity.Information;
    const range = new vscode.Range(f.range.startLine, f.range.startCol, f.range.endLine, f.range.endCol);
    const d = new vscode.Diagnostic(range, f.message, sev);
    d.source = "cfn-name-check";
    d.code = `${f.resourceType}/${f.property}`;
    diags.push(d);
  }
  return diags;
}

// ---------------------------------------------------------------- checking
async function checkDocument(doc: vscode.TextDocument) {
  if (!looksLikeTemplate(doc)) {
    diagnostics.delete(doc.uri);
    findingsByUri.delete(doc.uri.toString());
    return;
  }
  if (!cachedPython) cachedPython = await resolvePython();
  if (!cachedPython) {
    statusBar.text = "$(warning) cfn-name-check: python pkg missing";
    statusBar.tooltip = "Install with: pip install <path-to>/cfn-name-check/python — or set cfnNameCheck.pythonPath";
    statusBar.show();
    return;
  }
  try {
    const { out, err } = await runCli(cachedPython, buildArgs(doc.fileName, true), doc.getText());
    if (err.trim()) output.appendLine(`stderr (${doc.fileName}): ${err.trim()}`);
    let results: FileResult[];
    try {
      results = JSON.parse(out);
    } catch {
      output.appendLine(`unparsable CLI output for ${doc.fileName}: ${out.slice(0, 500)}`);
      statusBar.text = "$(warning) cfn-name-check: CLI error (see Output)";
      statusBar.show();
      return;
    }
    const r = results[0];
    if (!r) return;
    findingsByUri.set(doc.uri.toString(), r.findings);
    diagnostics.set(doc.uri, toDiagnostics(r));
    const errs = r.findings.filter((f) => f.severity === "error").length;
    const warns = r.findings.filter((f) => f.severity === "warning").length;
    statusBar.text = errs
      ? `$(error) names: ${errs} over limit`
      : warns
      ? `$(warning) names: ${warns} near limit`
      : `$(check) names ok`;
    statusBar.tooltip = r.budgetMode
      ? "cfn-name-check — budget mode (no stack name resolved; set cfnNameCheck.stackName or use the Description convention)"
      : `cfn-name-check — stack: ${r.stackName}`;
    statusBar.show();
  } catch (e: any) {
    output.appendLine(`check failed for ${doc.fileName}: ${e?.message ?? e}`);
  }
}

function scheduleCheck(doc: vscode.TextDocument) {
  const key = doc.uri.toString();
  clearTimeout(timers.get(key));
  timers.set(key, setTimeout(() => checkDocument(doc), cfg("debounceMs", 400)));
}

// ---------------------------------------------------------------- quick fixes
class NameFixProvider implements vscode.CodeActionProvider {
  provideCodeActions(doc: vscode.TextDocument, range: vscode.Range): vscode.CodeAction[] {
    const findings = findingsByUri.get(doc.uri.toString()) ?? [];
    const actions: vscode.CodeAction[] = [];
    for (const f of findings) {
      const fr = new vscode.Range(f.range.startLine, f.range.startCol, f.range.endLine, f.range.endCol);
      if (!fr.intersection(range) || f.severity === "info") continue;

      if (f.removable && f.removeRange) {
        const a = new vscode.CodeAction(
          `Remove ${f.property} — let CloudFormation auto-generate (always fits)`,
          vscode.CodeActionKind.QuickFix
        );
        a.edit = new vscode.WorkspaceEdit();
        const delRange = new vscode.Range(
          f.removeRange.startLine, 0,
          Math.min(f.removeRange.endLine + 1, doc.lineCount), 0
        );
        a.edit.delete(doc.uri, delRange);
        a.diagnostics = [];
        a.isPreferred = f.severity === "error";
        actions.push(a);
      }

      const copy = new vscode.CodeAction(
        `Copy worst-case name (${f.length ?? "?"}/${f.limit})`,
        vscode.CodeActionKind.Empty
      );
      copy.command = {
        command: "cfnNameCheck.copyWorstCase",
        title: "Copy worst-case name",
        arguments: [f.worstCase],
      };
      actions.push(copy);
    }
    return actions;
  }
}

// ---------------------------------------------------------------- hover
class NameHoverProvider implements vscode.HoverProvider {
  provideHover(doc: vscode.TextDocument, pos: vscode.Position): vscode.Hover | undefined {
    const findings = findingsByUri.get(doc.uri.toString()) ?? [];
    for (const f of findings) {
      const fr = new vscode.Range(f.range.startLine, f.range.startCol, f.range.endLine, f.range.endCol);
      if (!fr.contains(pos)) continue;
      const md = new vscode.MarkdownString();
      md.appendMarkdown(`**${f.resource}.${f.property}** — ${f.resourceType}\n\n`);
      if (f.length !== null) {
        md.appendMarkdown(`Worst case: \`${f.worstCase}\`\n\n`);
        md.appendMarkdown(`Length **${f.length}** / limit **${f.limit}** (margin ${f.margin})\n\n`);
      } else {
        md.appendMarkdown(`${f.message}\n\n`);
      }
      if (f.maxStackNameLen !== null) {
        md.appendMarkdown(`Max stack-name length this property tolerates: **${f.maxStackNameLen}**\n\n`);
      }
      if (f.unresolved) md.appendMarkdown(`_Contains unresolved tokens — length is a lower bound._`);
      return new vscode.Hover(md, fr);
    }
    return undefined;
  }
}

// ---------------------------------------------------------------- workspace scan
async function scanWorkspace() {
  if (!cachedPython) cachedPython = await resolvePython();
  if (!cachedPython) {
    vscode.window.showErrorMessage(
      "cfn-name-check Python package not found. pip install it, or set cfnNameCheck.pythonPath."
    );
    return;
  }
  const uris = await vscode.workspace.findFiles(
    "**/*.{yml,yaml,template,json}",
    "**/{node_modules,.git,cdk.out}/**"
  );
  output.clear();
  output.show(true);
  output.appendLine(`cfn-name-check workspace scan — ${uris.length} candidate file(s)\n`);
  let templates = 0, errors = 0, warnings = 0;
  for (const uri of uris) {
    const doc = await vscode.workspace.openTextDocument(uri);
    if (!looksLikeTemplate(doc)) continue;
    templates++;
    const { out } = await runCli(cachedPython, buildArgs(doc.fileName, true), doc.getText());
    try {
      const r: FileResult = JSON.parse(out)[0];
      findingsByUri.set(uri.toString(), r.findings);
      diagnostics.set(uri, toDiagnostics(r));
      const rel = vscode.workspace.asRelativePath(uri);
      const bad = r.findings.filter((f) => f.severity !== "info");
      output.appendLine(`${rel}  (stack: ${r.stackName ?? "budget mode"})`);
      if (!bad.length) output.appendLine("  ok");
      for (const f of bad) {
        if (f.severity === "error") errors++;
        else warnings++;
        output.appendLine(`  ${f.severity.toUpperCase().padEnd(7)} L${f.range.startLine + 1} ${f.resource}.${f.property}: ${f.message}`);
      }
      output.appendLine("");
    } catch {
      /* ignore unparsable */
    }
  }
  output.appendLine(`— ${templates} template(s): ${errors} error(s), ${warnings} warning(s)`);
  vscode.window.showInformationMessage(
    `cfn-name-check: ${templates} templates scanned — ${errors} error(s), ${warnings} warning(s). See Output panel.`
  );
}

// ---------------------------------------------------------------- activate
export function activate(context: vscode.ExtensionContext) {
  diagnostics = vscode.languages.createDiagnosticCollection("cfn-name-check");
  output = vscode.window.createOutputChannel("CFN Name Check");
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);

  context.subscriptions.push(
    diagnostics,
    output,
    statusBar,
    vscode.workspace.onDidOpenTextDocument(scheduleCheck),
    vscode.workspace.onDidChangeTextDocument((e) => scheduleCheck(e.document)),
    vscode.workspace.onDidSaveTextDocument(checkDocument),
    vscode.workspace.onDidCloseTextDocument((d) => {
      diagnostics.delete(d.uri);
      findingsByUri.delete(d.uri.toString());
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("cfnNameCheck")) {
        cachedPython = null;
        vscode.workspace.textDocuments.forEach(scheduleCheck);
      }
    }),
    vscode.languages.registerCodeActionsProvider(
      [{ language: "yaml" }, { language: "json" }],
      new NameFixProvider(),
      { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }
    ),
    vscode.languages.registerHoverProvider(
      [{ language: "yaml" }, { language: "json" }],
      new NameHoverProvider()
    ),
    vscode.commands.registerCommand("cfnNameCheck.scanWorkspace", scanWorkspace),
    vscode.commands.registerCommand("cfnNameCheck.checkCurrentFile", () => {
      const doc = vscode.window.activeTextEditor?.document;
      if (doc) checkDocument(doc);
    }),
    vscode.commands.registerCommand("cfnNameCheck.checkForCoreUpdate", async () => {
      await context.globalState.update("lastUpdateCheck", 0);
      await context.globalState.update("skippedVersion", undefined);
      await maybeCheckForCoreUpdate(context.globalState);
      vscode.window.setStatusBarMessage(`cfn-name-check core: ${coreVersion ?? "not found"}`, 5000);
    }),
    vscode.commands.registerCommand("cfnNameCheck.copyWorstCase", (s: string) => {
      vscode.env.clipboard.writeText(s);
      vscode.window.showInformationMessage("Worst-case name copied to clipboard.");
    })
  );

  vscode.workspace.textDocuments.forEach(scheduleCheck);
  setTimeout(() => maybeCheckForCoreUpdate(context.globalState), 5000);
}

export function deactivate() {}
