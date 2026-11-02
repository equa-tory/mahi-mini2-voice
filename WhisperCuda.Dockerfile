FROM nvidia/cuda:12.8.1-devel-ubuntu22.04 AS build
ARG WHISPER_COMMIT=1b863ea78d11a05f88d90fb959746ff024cb1a9d
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl cmake g++ make && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL https://github.com/ggml-org/whisper.cpp/archive/${WHISPER_COMMIT}.tar.gz | tar xz --strip-components=1
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=61 -DGGML_CUDA_FA=OFF -DGGML_NATIVE=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=ON && cmake --build build --target whisper-server -j4
FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/bin/whisper-server /usr/local/bin/whisper-server
USER 1000:1000
ENTRYPOINT ["whisper-server"]
CMD ["--host", "127.0.0.1", "--port", "8788", "--model", "/models/ggml-small-q5_1.bin", "--threads", "4", "--no-flash-attn", "--no-fallback", "--best-of", "1", "--beam-size", "1", "--language", "ru"]
