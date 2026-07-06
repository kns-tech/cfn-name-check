"""cfn-name-check core engine.

Loads a CloudFormation template (YAML with short-form tags, or JSON),
resolves every known *name* property to its worst-case string, and checks
it against the service limit.

Resolution rules:
  ${AWS::StackName}   -> stack name from context (Description regex, CLI, or budget mode)
  !Ref <Parameter>    -> longest AllowedValue, else Default, else MaxLength placeholder
  !If                 -> exact mode: only branches reachable under some combination of
                         AllowedValues are considered (conditions are evaluated);
                         strict mode / non-enumerable params: longest branch wins
  !Ref/!GetAtt <Resource> -> recursively resolve that resource's own name property;
                         unnamed resources count as CFN-generated (safe, truncated)
  !Sub / !Join / !Select  -> resolved structurally
"""
from __future__ import annotations
import itertools, json, re
import yaml

MAX_CONDITION_COMBOS = 512
LONGEST_REGION = "ap-southeast-1"  # 14 chars; representative worst case
ACCOUNT_ID = "123456789012"

# ------------------------------------------------------------------ YAML load
class CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    key = "Ref" if suffix == "Ref" else ("Condition" if suffix == "Condition" else "Fn::" + suffix)
    if suffix == "GetAtt" and isinstance(node, yaml.ScalarNode):
        return {"Fn::GetAtt": loader.construct_scalar(node).split(".")}
    if isinstance(node, yaml.ScalarNode):
        return {key: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {key: loader.construct_sequence(node, deep=True)}
    return {key: loader.construct_mapping(node, deep=True)}


for _t in ("Ref", "Sub", "If", "Join", "Select", "Split", "GetAtt", "ImportValue",
           "FindInMap", "Equals", "Not", "And", "Or", "Condition", "Base64",
           "Cidr", "GetAZs", "Length", "ToJsonString"):
    CfnLoader.add_multi_constructor("!" + _t, lambda l, s, n, t=_t: _tag(l, t, n))


def load_template(text: str):
    data = yaml.load(text, Loader=CfnLoader)
    node = yaml.compose(text, Loader=CfnLoader)  # parallel node graph for positions
    return data, node


# ------------------------------------------------------------------ positions
def find_node(node, path):
    """Navigate a composed YAML node graph by path (str keys / int indices).
    Returns (key_node, value_node) for the final mapping key, else (None, value)."""
    key_node = None
    for part in path:
        if isinstance(node, yaml.MappingNode) and isinstance(part, str):
            for k, v in node.value:
                if getattr(k, "value", None) == part:
                    key_node, node = k, v
                    break
            else:
                return None, None
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            if part >= len(node.value):
                return None, None
            key_node, node = None, node.value[part]
        else:
            return None, None
    return key_node, node


def node_range(key_node, value_node):
    """(startLine, startCol, endLine, endCol) 0-based."""
    if key_node is not None:
        s = key_node.start_mark
    elif value_node is not None:
        s = value_node.start_mark
    else:
        return (0, 0, 0, 1)
    e = value_node.end_mark if value_node is not None else key_node.end_mark
    return (s.line, s.column, e.line, e.column)


# ------------------------------------------------------------------ conditions
class ConditionEvaluator:
    """Enumerates AllowedValues combinations of parameters referenced by
    Conditions; yields per-combo truth assignments so !If branches that can
    never occur are pruned."""

    def __init__(self, template):
        self.conditions = template.get("Conditions") or {}
        self.params = template.get("Parameters") or {}

    def _refs_in(self, node, acc):
        if isinstance(node, dict):
            if "Ref" in node and isinstance(node["Ref"], str):
                acc.add(node["Ref"])
            for v in node.values():
                self._refs_in(v, acc)
        elif isinstance(node, list):
            for v in node:
                self._refs_in(v, acc)

    def combos(self):
        """Yield dicts {param: value} covering all AllowedValues combinations,
        or None (meaning: cannot enumerate -> caller falls back to longest-branch)."""
        refs = set()
        self._refs_in(self.conditions, refs)
        refs = [r for r in refs if not r.startswith("AWS::")]
        enumerable = [r for r in refs
                      if (self.params.get(r) or {}).get("AllowedValues")]
        # Params without AllowedValues stay unassigned: conditions touching them
        # evaluate to None and fall back to longest-branch individually.
        if not enumerable or not self.conditions:
            return None
        value_lists = [(p, self.params[p]["AllowedValues"]) for p in enumerable]
        total = 1
        for _, vals in value_lists:
            total *= len(vals)
        if total > MAX_CONDITION_COMBOS:
            return None
        out = []
        for combo in itertools.product(*[vals for _, vals in value_lists]):
            out.append({p: str(v) for (p, _), v in zip(value_lists, combo)})
        return out

    def eval_condition(self, name_or_expr, assignment):
        if isinstance(name_or_expr, str):
            expr = self.conditions.get(name_or_expr)
            if expr is None:
                return None
            return self.eval_condition(expr, assignment)
        e = name_or_expr
        if isinstance(e, dict):
            if "Fn::Equals" in e:
                a, b = e["Fn::Equals"]
                ra, rb = self._scalar(a, assignment), self._scalar(b, assignment)
                if ra is None or rb is None:
                    return None
                return ra == rb
            if "Fn::Not" in e:
                r = self.eval_condition(e["Fn::Not"][0], assignment)
                return None if r is None else (not r)
            if "Fn::And" in e:
                rs = [self.eval_condition(x, assignment) for x in e["Fn::And"]]
                return None if None in rs else all(rs)
            if "Fn::Or" in e:
                rs = [self.eval_condition(x, assignment) for x in e["Fn::Or"]]
                return None if None in rs else any(rs)
            if "Condition" in e:
                return self.eval_condition(e["Condition"], assignment)
        return None

    def _scalar(self, x, assignment):
        if isinstance(x, (str, int, float)):
            return str(x)
        if isinstance(x, dict) and "Ref" in x:
            return assignment.get(x["Ref"])
        return None


# ------------------------------------------------------------------ resolver
class Resolver:
    def __init__(self, template, stack_name, strict=False):
        self.tpl = template
        self.stack = stack_name          # None => budget mode
        self.strict = strict
        self.params = template.get("Parameters") or {}
        self.resources = template.get("Resources") or {}
        self.cond_eval = ConditionEvaluator(template)
        self._combos = None if strict else self.cond_eval.combos()
        self._res_name_cache = {}
        self.unknown = False             # set when a placeholder was used
        self.unknown_names = set()       # which deploy-time values were involved

    # ---- parameters -------------------------------------------------------
    def _param_value(self, name, assignment):
        if assignment and name in assignment:
            return assignment[name]
        p = self.params.get(name, {})
        av = p.get("AllowedValues")
        if av:
            return max((str(v) for v in av), key=len)
        if "Default" in p and p["Default"] is not None:
            return str(p["Default"])
        if "MaxLength" in p:
            return "?" * int(p["MaxLength"])
        self.unknown = True
        self.unknown_names.add(name)
        return "\u00ab%s\u00bb" % name

    # ---- resources referenced by name --------------------------------------
    def _resource_name(self, logical_id, assignment, seen):
        if logical_id in seen:
            self.unknown = True
            return "\u00abcycle:%s\u00bb" % logical_id
        res = self.resources.get(logical_id)
        if not isinstance(res, dict):
            self.unknown = True
            return "\u00abres:%s\u00bb" % logical_id
        from .limits import LIMITS
        props = res.get("Properties") or {}
        for path, _limit, _note, _c in LIMITS.get(res.get("Type", ""), []):
            head = path.split(".")[0].replace("[]", "")
            if "[]" not in path and head in props:
                return self._resolve(props[head], assignment, seen | {logical_id})
        # No explicit name: CloudFormation generates + truncates -> safe;
        # model it as "{stack}-{logicalid}-ABC123XYZ45" capped at 64 for realism.
        base = (self.stack or "\u00abstack\u00bb") + "-" + logical_id + "-" + "X" * 12
        return base[:64]

    # ---- main resolve -------------------------------------------------------
    def _resolve(self, node, assignment, seen=frozenset()):
        if node is None:
            return ""
        if isinstance(node, (str, int, float, bool)):
            return str(node)
        if isinstance(node, dict):
            if "Ref" in node:
                r = node["Ref"]
                if r == "AWS::StackName":
                    if self.stack is None:
                        return "\u2605STACK\u2605"
                    return self.stack
                if r == "AWS::Region":
                    return LONGEST_REGION
                if r == "AWS::AccountId":
                    return ACCOUNT_ID
                if r == "AWS::Partition":
                    return "aws"
                if r == "AWS::URLSuffix":
                    return "amazonaws.com"
                if r in self.params:
                    return self._param_value(r, assignment)
                if r in self.resources:
                    return self._resource_name(r, assignment, seen)
                self.unknown = True
                return "\u00abref:%s\u00bb" % r
            if "Fn::GetAtt" in node:
                target = node["Fn::GetAtt"]
                lid = target[0] if isinstance(target, list) else str(target).split(".")[0]
                if lid in self.resources:
                    return self._resource_name(lid, assignment, seen)
                self.unknown = True
                return "\u00abgetatt\u00bb"
            if "Fn::Sub" in node:
                v = node["Fn::Sub"]
                if isinstance(v, str):
                    tpl, varmap = v, {}
                else:
                    tpl = v[0]
                    varmap = v[1] if len(v) > 1 and isinstance(v[1], dict) else {}

                def repl(m):
                    key = m.group(1)
                    if key.startswith("!"):
                        return "${" + key[1:] + "}"  # literal ${!x}
                    if key in varmap:
                        return self._resolve(varmap[key], assignment, seen)
                    if "." in key:  # GetAtt shorthand inside Sub
                        return self._resolve({"Fn::GetAtt": key.split(".")}, assignment, seen)
                    return self._resolve({"Ref": key}, assignment, seen)

                return re.sub(r"\$\{([^}]+)\}", repl, tpl)
            if "Fn::If" in node:
                cname, a, b = node["Fn::If"]
                truth = self.cond_eval.eval_condition(cname, assignment) if assignment is not None else None
                if truth is True:
                    return self._resolve(a, assignment, seen)
                if truth is False:
                    return self._resolve(b, assignment, seen)
                ra = self._resolve(a, assignment, seen)
                rb = self._resolve(b, assignment, seen)
                return ra if len(ra) >= len(rb) else rb
            if "Fn::Join" in node:
                sep, items = node["Fn::Join"]
                return str(sep).join(self._resolve(i, assignment, seen) for i in items)
            if "Fn::Select" in node:
                idx, lst = node["Fn::Select"]
                try:
                    if isinstance(lst, dict) and "Fn::Split" in lst:
                        d, s = lst["Fn::Split"]
                        parts = self._resolve(s, assignment, seen).split(str(d))
                        return parts[int(self._resolve(idx, assignment, seen))]
                    return self._resolve(lst[int(self._resolve(idx, assignment, seen))], assignment, seen)
                except Exception:
                    self.unknown = True
                    return "\u00abselect\u00bb"
            if "Fn::FindInMap" in node:
                try:
                    mname, k1, k2 = (self._resolve(x, assignment, seen) for x in node["Fn::FindInMap"][:3])
                    return str(self.tpl["Mappings"][mname][k1][k2])
                except Exception:
                    self.unknown = True
                    return "\u00abmap\u00bb"
            if "Fn::ImportValue" in node:
                self.unknown = True
                return "\u00abimport\u00bb"
        self.unknown = True
        return "\u00abexpr\u00bb"

    def worst_case(self, expr):
        """Return (worst_string, had_unknown). Exact mode enumerates condition
        combos; strict/fallback mode uses longest-branch."""
        self.unknown = False
        self.unknown_names = set()
        if self._combos:
            best = ""
            for assignment in self._combos:
                s = self._resolve(expr, assignment)
                if len(s) > len(best):
                    best = s
            return best, self.unknown
        return self._resolve(expr, None), self.unknown


# ------------------------------------------------------------------ checking
DESCRIPTION_REGEX_DEFAULT = r"StackName=([^\s|,;]+)"


def extract_stack_name(template, description_regex=DESCRIPTION_REGEX_DEFAULT):
    desc = template.get("Description") or ""
    if isinstance(desc, str) and description_regex:
        m = re.search(description_regex, desc)
        if m:
            return m.group(1)
    return None


def _iter_name_props(rtype, props, limits_db):
    """Yield (path_list, expr, limit, note, constraints)."""
    for path, limit, note, cons in limits_db.get(rtype, []):
        if cons.get("skip"):
            continue
        parts = path.split(".")
        if "[]" not in path:
            if parts[0] in props:
                node = props
                ok = True
                for p in parts:
                    if isinstance(node, dict) and p in node:
                        node = node[p]
                    else:
                        ok = False
                        break
                if ok:
                    yield parts, node, limit, note, cons
        else:
            list_key = parts[0].replace("[]", "")
            sub_key = parts[1]
            for i, item in enumerate(props.get(list_key) or []):
                if isinstance(item, dict) and sub_key in item:
                    yield [list_key, i, sub_key], item[sub_key], limit, note, cons


MAX_STACK_NAME_LEN = 128  # CloudFormation hard cap on stack names


def check_template(text, filename="<stdin>", stack_name=None,
                   description_regex=DESCRIPTION_REGEX_DEFAULT,
                   warn_margin=5, strict=False, extra_limits=None,
                   deploy_time_threshold=64):
    from . import limits as limits_mod
    if extra_limits:
        limits_mod.merge_extra(extra_limits)
    limits_db = limits_mod.LIMITS

    data, root_node = load_template(text)
    if not isinstance(data, dict) or "Resources" not in data:
        return {"file": filename, "isTemplate": False, "findings": []}

    resolved_stack = stack_name or extract_stack_name(data, description_regex)
    budget_mode = resolved_stack is None
    resolver = Resolver(data, resolved_stack, strict=strict)

    findings = []
    for lid, res in (data.get("Resources") or {}).items():
        if not isinstance(res, dict):
            continue
        rtype = res.get("Type", "")
        props = res.get("Properties") or {}
        for path, expr, limit, note, cons in _iter_name_props(rtype, props, limits_db):
            worst, unknown = resolver.worst_case(expr)
            raw = json.dumps(expr, default=str)
            uses_stack = "AWS::StackName" in raw

            if budget_mode and uses_stack:
                overhead = len(worst) - len("\u2605STACK\u2605")
                budget = limit - overhead
                length, margin = None, None
                if budget <= 0:
                    severity = "error"
                elif budget < MAX_STACK_NAME_LEN:
                    severity = "warning"  # a legal stack name could overflow this
                else:
                    severity = "info"     # even a 128-char stack name fits
                msg = (f"{note}: stack name unknown until deploy time; supports stack names up to "
                       f"{budget} chars (limit {limit}, fixed part {overhead} chars). "
                       f"Worst-case pattern: {worst.replace(chr(0x2605)+'STACK'+chr(0x2605), '{STACK}')}")
            else:
                length = len(worst)
                margin = limit - length
                budget = (limit - (length - len(resolved_stack))) if (uses_stack and resolved_stack) else None
                if unknown:
                    import re as _re
                    fixed = _re.sub(r"\u00ab[^\u00bb]*\u00bb", "", worst)
                    remaining = limit - len(fixed)
                    tokens = ", ".join(sorted(resolver.unknown_names)) or "deploy-time value"
                    if remaining <= 0:
                        severity = "error"
                        msg = (f"{note}: fixed part alone is {len(fixed)}/{limit} chars \u2014 "
                               f"exceeds the limit before the deploy-time value ({tokens}) is even added. "
                               f"Pattern: \u201c{worst}\u201d")
                    elif remaining < deploy_time_threshold:
                        severity = "warning"
                        msg = (f"{note} depends on a value provided at deploy time ({tokens}): "
                               f"fixed part is {len(fixed)}/{limit} chars, leaving only "
                               f"{remaining} chars for it. Worst-case pattern: \u201c{worst}\u201d")
                    else:
                        severity = "info"
                        msg = (f"{note}: depends on deploy-time value ({tokens}); "
                               f"{remaining} chars available for it (limit {limit}).")
                elif margin < 0:
                    severity = "error"
                    msg = (f"{note} exceeds {limit}-char limit: worst case is {length} chars "
                           f"(\u201c{worst}\u201d).")
                    if budget is not None:
                        msg += f" Max stack name this tolerates: {budget} chars."
                elif margin <= warn_margin:
                    severity = "warning"
                    msg = (f"{note} is {length}/{limit} chars in the worst case "
                           f"(only {margin} to spare: \u201c{worst}\u201d).")
                    if budget is not None:
                        msg += f" Max stack name: {budget} chars."
                else:
                    severity = "info"
                    msg = f"{note}: worst case {length}/{limit} chars."
                if cons.get("lowercase") and any(c.isupper() for c in worst if c.isalpha()):
                    if "\u00ab" not in worst:
                        findings.append(_mk(filename, root_node, lid, path, "error",
                                            f"{note} must be lowercase but worst case contains "
                                            f"uppercase characters: \u201c{worst}\u201d.",
                                            worst, length, limit, margin, budget, unknown, rtype))

            findings.append(_mk(filename, root_node, lid, path, severity, msg,
                                worst, length, limit, margin, budget, unknown, rtype))
    return {"file": filename, "isTemplate": True,
            "stackName": resolved_stack, "budgetMode": budget_mode,
            "findings": findings}


def _mk(filename, root_node, lid, path, severity, msg, worst, length, limit,
        margin, budget, unknown, rtype):
    full_path = ["Resources", lid, "Properties"] + list(path)
    key_node, value_node = find_node(root_node, full_path)
    sl, sc, el, ec = node_range(key_node, value_node)
    # range of the whole property entry (for the "remove property" quick fix)
    remove_range = None
    if key_node is not None and value_node is not None:
        remove_range = {"startLine": key_node.start_mark.line, "startCol": 0,
                        "endLine": value_node.end_mark.line,
                        "endCol": value_node.end_mark.column}
    prop_str = ".".join(str(p) if not isinstance(p, int) else f"[{p}]" for p in path)
    return {
        "file": filename, "resource": lid, "resourceType": rtype,
        "property": prop_str, "severity": severity, "message": msg,
        "worstCase": worst, "length": length, "limit": limit, "margin": margin,
        "maxStackNameLen": budget, "unresolved": unknown,
        "range": {"startLine": sl, "startCol": sc, "endLine": el, "endCol": ec},
        "removeRange": remove_range,
        "removable": severity in ("error", "warning") and not isinstance(path[-1], int),
    }
