import re
from typing import List, Dict, Any
from asteval import Interpreter

class Rule:
    def __init__(self, expr: str, action: str, comment: str = ""):
        self.expr = expr.strip()
        self.action = action.strip().upper()
        self.comment = comment.strip()

    def evaluate(self, context: "asteval.Interpreter") -> bool:
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
        self.rules = []
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # expected: IF <expr> THEN <ACTION> # optional comment
                m = re.match(r'IF\s+(.+)\s+THEN\s+(\w+)(?:\s*#(.*))?', line, re.IGNORECASE)
                if m:
                    expr, action, comment = m.group(1), m.group(2), m.group(3) or ''
                    # asteval supports 'and'/'or', so we ensure consistency
                    expr = expr.replace('AND', 'and').replace('OR', 'or')
                    self.rules.append(Rule(expr, action, comment))

    def evaluate(self, indicators: Dict[str, Any], fundamentals: Dict[str, Any]) -> Dict[str, Any]:
        # Clear previous symbols and load the new context into the interpreter
        self.interp.symtable.clear()
        
        # Add indicators to the context
        for k, v in indicators.items():
            # asteval handles None and different numeric types gracefully
            self.interp.symtable[k] = v

        # Add fundamentals, prefixed with F_ to avoid naming collisions
        for k, v in fundamentals.items():
            self.interp.symtable[f'F_{k}'] = v if v is not None else 0.0

        triggered = []
        for r in self.rules:
            if r.evaluate(self.interp):
                triggered.append({'action': r.action, 'expr': r.expr, 'comment': r.comment})

        decision = 'HOLD'
        reason = ''
        # Simple aggregation: if any SELL present, SELL; elif any BUY present, BUY; else HOLD
        actions = [t['action'] for t in triggered]
        if 'SELL' in actions:
            decision = 'SELL'
        elif 'BUY' in actions or 'ACHETER' in actions or 'BACK' in actions:
            decision = 'BUY'

        if triggered:
            reason = '; '.join([f"{t['action']}: {t['expr']} ({t['comment']})" for t in triggered])

        return {'decision': decision, 'reason': reason, 'triggered': triggered}
