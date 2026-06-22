def moving_average(xs, window):
    """Return the average of each consecutive `window` items in xs."""
    out = []
    for i in range(len(xs)):
        out.append(sum(xs[i:i + window]) / window)
    return out
