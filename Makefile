PY ?= python3
.PHONY: install test demo real privacy-check benchmark sim tour doctor clean
install:        ; $(PY) -m pip install -e . --break-system-packages
test:           ; $(PY) -m unittest discover -s tests -t . -v
demo:           ; $(PY) -m slothwatch.demo.cli all
tour:           ; $(PY) -m slothwatch.demo.cli tour --pace 3
fast:           ; $(PY) -m slothwatch.demo.cli fast everything-wrong --pace 2
sim:            ; $(PY) -m slothwatch.demo.cli sim
real:           ; @echo "Usage: python3 -m slothwatch run <csv-or-folder>  then  python3 -m slothwatch dashboard"
privacy-check:  ; $(PY) -m slothwatch privacy-check
benchmark:      ; $(PY) -m slothwatch bench
doctor:         ; $(PY) -m slothwatch doctor
clean:          ; rm -rf .slothwatch .slothwatch-demo
