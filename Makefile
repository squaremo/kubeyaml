.PHONY: all clean

all: .uptodate.kubeyaml

clean:
	rm -f .uptodate.kubeyaml
	rm -f kubeyaml.tar.gz
	rm -rf ./dist
	rm -rf ./build

kubeyaml.tar.gz: kubeyaml.py kubeyaml.spec requirements.txt
	mkdir -p build
	cp $^ build/
	docker run --rm -v "$(shell pwd)/build:/src" six8/pyinstaller-alpine \
		--noconfirm --clean \
		kubeyaml.py
	tar -C build/dist/kubeyaml -cz -f "$@" .

.uptodate.kubeyaml: kubeyaml.tar.gz Dockerfile
	docker build -t squaremo/kubeyaml .
	touch $@
