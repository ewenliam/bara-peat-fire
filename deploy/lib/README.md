# libgomp.so.1

LightGBM needs the GNU OpenMP runtime (`libgomp.so.1`). The AWS Lambda Python 3.12 image, the
closest stand-in for Vercel's Python runtime, does not include it, so the Vercel bundle carries this
copy and loads it only when the system library is missing (`deploy/vercel_bundle.py`, `app.py`).

Source: the `libgomp` package of the official `amazonlinux:2023` image (glibc 2.34), copied from
`/usr/lib64/libgomp.so.1.0.0`. It is part of GCC and is distributed under the GPL version 3 with
the GCC Runtime Library Exception, which allows redistribution alongside other programs.
