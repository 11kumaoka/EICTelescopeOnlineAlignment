"""Shared Pede result reader; no solver, Geometry or Controller dependencies."""

def read_pede(path):
    out={}
    for line in path.read_text().splitlines():
        fields=line.split()
        if len(fields)>1:
            try:out[int(fields[0])]=float(fields[1])
            except ValueError:pass
    return out
