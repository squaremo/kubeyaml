.PHONY: all clean test

all: .uptodate.kubeyaml

clean:
	rm -f .uptodate.kubeyaml
	rm -f kubeyaml.tar.gz
	rm -rf ./dist
	rm -rf ./build

.uptodate.kubeyaml: Dockerfile kubeyaml.py requirements.txt
	mkdir -p build
	cp $^ build/
	docker build -t squaremo/kubeyaml ./build/
	touch $@

test:
	pytest --hypothesis-show-statistics test_*
