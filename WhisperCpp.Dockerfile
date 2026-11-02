FROM debian:bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl cmake g++ make && rm -rf /var/lib/apt/lists/*
WORKDIR /src
ARG WHISPER_COMMIT=1b863ea78d11a05f88d90fb959746ff024cb1a9d
RUN curl -fsSL https://github.com/ggml-org/whisper.cpp/archive/${WHISPER_COMMIT}.tar.gz | tar xz --strip-components=1
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=OFF -DGGML_NATIVE=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=ON && cmake --build build --target whisper-server -j4
COPY whisper-unload.inc /tmp/whisper-unload.inc
RUN sed -i '/    svr->Post(sparams.request_path + "\/load"/e cat /tmp/whisper-unload.inc' examples/server/server.cpp && sed -i 's/whisper_free(ctx);/if (ctx) whisper_free(ctx);/g; /        \/\/ first check user requested fields/i\        if (!ctx) { res.status = 503; res.set_content("Model unloaded", "text/plain"); return; }' examples/server/server.cpp && sed -i '/std::string model = req.get_file_value/a\        active_model = model;' examples/server/server.cpp && cmake --build build --target whisper-server -j4

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 libatomic1 && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bin/whisper-server /usr/local/bin/whisper-server
USER 1000:1000
ENTRYPOINT ["whisper-server"]
CMD ["--host", "127.0.0.1", "--port", "8788", "--model", "/models/ggml-small-q5_1.bin", "--threads", "4", "--no-gpu", "--no-fallback", "--best-of", "1", "--beam-size", "1", "--language", "ru"]
