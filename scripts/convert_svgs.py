import sys
try:
    import cairosvg
except Exception as e:
    print('cairosvg import error:', e)
    sys.exit(2)

pairs = [
    ('docs/architecture.svg','docs/architecture.png'),
    ('docs/sequence.svg','docs/sequence.png'),
]
for src,dst in pairs:
    try:
        cairosvg.svg2png(url=src, write_to=dst)
        print(f'Converted {src} -> {dst}')
    except Exception as e:
        print(f'Failed to convert {src}: {e}')
        sys.exit(3)
print('All done')
