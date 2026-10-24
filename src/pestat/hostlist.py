"""Expand Slurm hostlist expressions."""


def expand(expr):
    """Expand a Slurm hostlist expression into host names.

    The result matches ``scontrol show hostnames``. Each bracket group
    expands as a cartesian product, and each range keeps the zero padding
    of its start.

    Parameters
    ----------
    expr : str
        Hostlist such as ``z[01-03,10],gpu1``.

    Returns
    -------
    list of str
        Host names in expansion order, duplicates kept.

    Raises
    ------
    ValueError
        If a bracket is unbalanced or a range runs backwards.

    Examples
    --------
    >>> expand("z[01-03,10],gpu1")
    ['z01', 'z02', 'z03', 'z10', 'gpu1']
    >>> expand("a[1-2]b[3-4]")
    ['a1b3', 'a1b4', 'a2b3', 'a2b4']
    """
    if "[" not in expr and "," not in expr:
        return [expr]
    hosts = []
    for name in _split(expr):
        hosts.extend(_expand_name(name))
    return hosts


def _split(expr):
    """Split a hostlist on the commas outside brackets.

    Parameters
    ----------
    expr : str
        Hostlist expression.

    Returns
    -------
    list of str
        Non-empty host name patterns.
    """
    names, depth, start = [], 0, 0
    for i, char in enumerate(expr):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            names.append(expr[start:i])
            start = i + 1
    if depth:
        raise ValueError(f"unbalanced brackets in hostlist {expr!r}")
    names.append(expr[start:])
    return [name for name in names if name]


def _expand_name(name):
    """Expand one host name pattern with zero or more bracket groups.

    Parameters
    ----------
    name : str
        Pattern without top-level commas, such as ``r1-n[01-02]``.

    Returns
    -------
    list of str
        Host names.
    """
    open_ = name.find("[")
    if open_ < 0:
        return [name]
    close = name.find("]", open_)
    if close < 0:
        raise ValueError(f"unbalanced brackets in hostlist {name!r}")
    prefix, ranges, rest = name[:open_], name[open_ + 1 : close], name[close + 1 :]
    tails = _expand_name(rest)
    return [prefix + value + tail for value in _values(ranges) for tail in tails]


def _values(ranges):
    """Expand the inside of one bracket group.

    Parameters
    ----------
    ranges : str
        Comma-separated numbers and ranges, such as ``01-03,10``.

    Returns
    -------
    list of str
        Zero-padded values.
    """
    values = []
    for part in ranges.split(","):
        first, _, last = part.partition("-")
        last = last or first
        if int(last) < int(first):
            raise ValueError(f"invalid hostlist range {part!r}")
        values.extend(
            str(n).zfill(len(first)) for n in range(int(first), int(last) + 1)
        )
    return values
