import re
from typing import List, Dict, Any
from asteval import Interpreter


class Rule:
    def __init__(self, expr: str, score: int = 0, action: str = "", comment: str = ""):
        self.expr = expr.strip()
        self.score = int(score)
        self.action = action.strip().upper() if action else ""
        self.comment = comment.strip()

    def evaluate(self, context: Interpreter) -> bool:
        """Evaluates the rule's expression using the provided asteval context."""
        try:
            return bool(context.eval(self.expr))
        except Exception:
            # Could log the error here for debugging
            return False


class DSLEngine:
    def __init__(self, rules_path: str = None):
        self.rules: List[Rule] = []
        # The asteval interpreter, created once
        self.interp = Interpreter()
        if rules_path:
            self.load_rules(rules_path)

    def load_rules(self, path: str):
        """Load rules from a DSL file.

        Supported rule formats:
        - IF <expr> THEN +N  # comment  (score)
        - IF <expr> THEN -N  # comment
        - IF <expr> THEN BUY/SELL/HOLD  # legacy (mapped to scores)
        """
        self.rules = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Normalize boolean operators
                line_norm = line.replace("AND", "and").replace("OR", "or")
                # Try to parse numeric score form: IF <expr> THEN +N # comment
                m_score = re.match(
                    r"IF\s+(.+)\s+THEN\s+([+-]?\d+)\s*(?:#(.*))?$",
                    line_norm,
                    re.IGNORECASE,
                )
                if m_score:
                    expr = m_score.group(1)
                    score = int(m_score.group(2))
                    comment = m_score.group(3) or ""
                    self.rules.append(
                        Rule(expr, score=score, action="", comment=comment)
                    )
                    continue

                # Fallback: IF <expr> THEN ACTION (BUY/SELL/HOLD)
                m = re.match(
                    r"IF\s+(.+)\s+THEN\s+(\w+)(?:\s*#(.*))?", line_norm, re.IGNORECASE
                )
                if m:
                    expr, action, comment = (
                        m.group(1),
                        m.group(2).upper(),
                        m.group(3) or "",
                    )
                    # Map legacy actions to scores
                    action_score_map = {
                        "BUY": 3,
                        "ACHETER": 3,
                        "SELL": -3,
                        "VENDRE": -3,
                        "HOLD": 0,
                    }
                    score = action_score_map.get(action, 0)
                    self.rules.append(
                        Rule(expr, score=score, action=action, comment=comment)
                    )

    def evaluate(
        self, indicators: Dict[str, Any], fundamentals: Dict[str, Any]
    ) -> Dict[str, Any]:
        # Clear previous symbols and load the new context into the interpreter
        self.interp.symtable.clear()

        # Add indicators to the context
        for k, v in indicators.items():
            # asteval handles None and different numeric types gracefully
            self.interp.symtable[k] = v

        # Add fundamentals, prefixed with F_ to avoid naming collisions
        for k, v in fundamentals.items():
            self.interp.symtable[f"F_{k}"] = v if v is not None else 0.0

        triggered = []
        total_score = 0
        for r in self.rules:
            try:
                hit = r.evaluate(self.interp)
            except Exception:
                hit = False
            if hit:
                total_score += int(r.score)
                triggered.append(
                    {
                        "action": r.action,
                        "expr": r.expr,
                        "comment": r.comment,
                        "score": r.score,
                    }
                )

        # Translate total_score into a decision and strength
        decision = "HOLD"
        strength = "neutral"
        if total_score >= 4:
            decision = "BUY"
            strength = "strong"
        elif total_score >= 2:
            decision = "BUY"
            strength = "normal"
        elif total_score <= -4:
            decision = "SELL"
            strength = "strong"
        elif total_score <= -2:
            decision = "SELL"
            strength = "normal"
        else:
            decision = "HOLD"
            strength = "neutral"

        # Build a human-readable reason summary including scores
        reason_parts = []
        for t in triggered:
            part = f"{t.get('score',0):+d}: {t['expr']}"
            if t.get("comment"):
                part += f" ({t['comment']})"
            reason_parts.append(part)
        reason = "; ".join(reason_parts)

        return {
            "decision": decision,
            "strength": strength,
            "score": total_score,
            "reason": reason,
            "triggered": triggered,
        }
