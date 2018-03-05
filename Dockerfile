FROM alpine:3.6
ENTRYPOINT ["/usr/lib/kubeyaml/kubeyaml"]
ADD ./kubeyaml.tar.gz /usr/lib/kubeyaml
