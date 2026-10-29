"""Evaluate the compact-XML arithmetic used by native telescope Geometry checks."""
import ast
import math


def xml_number(expr, constants):
    """Evaluate only arithmetic/constants used by this specific compact XML."""
    units = {"mm": 1., "cm": 10., "m": 1000., "um": .001, "pi": math.pi}

    def walk(node, stack):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in units:
                return units[node.id]
            if node.id not in constants or node.id in stack:
                raise ValueError(f"Unsupported or recursive XML symbol: {node.id}")
            return walk(ast.parse(constants[node.id], mode="eval").body, stack | {node.id})
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = walk(node.operand, stack)
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = walk(node.left, stack), walk(node.right, stack)
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            return a / b
        raise ValueError(f"Unsupported XML arithmetic: {expr}")

    return walk(ast.parse(expr, mode="eval").body, set())
