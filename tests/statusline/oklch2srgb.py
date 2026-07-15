#!/usr/bin/env python3
"""One-shot oklch(L% C H) -> sRGB 8-bit converter.
Source: Bjorn Ottosson, "A perceptual color space for image processing",
https://bottosson.github.io/posts/oklab/ (OKLab<->linear-sRGB matrices,
section "Converting from linear sRGB to OKLab" inverted here) combined with
the standard sRGB EOTF (IEC 61966-2-1) for the linear->gamma step.
"""
import math

def oklch_to_srgb(L, C, H_deg):
    h = math.radians(H_deg)
    a = C * math.cos(h)
    b = C * math.sin(h)

    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l = l_ ** 3
    m = m_ ** 3
    s = s_ ** 3

    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bb = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s

    def gamma(c):
        c = max(0.0, min(1.0, c))
        if c <= 0.0031308:
            return c * 12.92
        return 1.055 * (c ** (1 / 2.4)) - 0.055

    R, G, B = gamma(r), gamma(g), gamma(bb)
    r8 = round(R * 255)
    g8 = round(G * 255)
    b8 = round(B * 255)
    return r8, g8, b8

if __name__ == "__main__":
    # HEAT_3: --accent anchor with chroma reduced 0.14 -> 0.08
    r, g, b = oklch_to_srgb(0.64, 0.08, 47)
    print(f"oklch(64% 0.08 47) -> rgb({r},{g},{b}) hex #{r:02x}{g:02x}{b:02x}")
    # sanity check against known --accent value: oklch(64% 0.14 47) -> should be ~#cf6e39 (207;110;57)
    r2, g2, b2 = oklch_to_srgb(0.64, 0.14, 47)
    print(f"sanity oklch(64% 0.14 47) -> rgb({r2},{g2},{b2}) hex #{r2:02x}{g2:02x}{b2:02x} (expect ~207;110;57)")
