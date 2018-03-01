.PHONY: all

all: .uptodate.kubeyaml

kubeyaml.tar.gz: kubeyaml.py kubeyaml.spec
	docker run --rm -v "$(shell pwd):/src/" cdrx/pyinstaller-linux:python3
	tar -C dist/kubeyaml -cz -f "$@" .

.uptodate.kubeyaml: kubeyaml.tar.gz Dockerfile
	docker build -t squaremo/kubeyaml .
	touch $@
