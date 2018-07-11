** Updating tracker.proto

    pbjs -t static-module -w closure --no-encode --no-create --no-verify --no-delimited --no-comments --no-beautify --keep-case trackers/trackers.proto -o trackers/static/trackers_pb.js
    protoc -I=trackers --python_out=trackers trackers/trackers.proto
