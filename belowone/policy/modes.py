"""Operator radius bounded by calibrated decision confidence."""

def effective_radius(operator_radius, confidence, *, threshold=.6):
    if type(operator_radius) is not int or operator_radius < 0 or not 0 <= confidence <= 1:
        raise ValueError('Invalid trace radius or confidence')
    return operator_radius // 2 if confidence < threshold else operator_radius


def validate_mode(mode):
    if mode not in {'verify', 'strict'}:
        raise ValueError('Response mode must be verify or strict')
    return mode
