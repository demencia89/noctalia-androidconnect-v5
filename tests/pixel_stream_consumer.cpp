// Integration reader: compile with upstream src/ipc/pixel_stream_client.cpp and src/core/log.cpp.
#include "ipc/pixel_stream_client.h"

#include <chrono>
#include <cstring>
#include <iostream>
#include <poll.h>
#include <thread>
#include <vector>

int main(int argc, char** argv) {
  if (argc != 2) return 2;
  PixelStreamClient client;
  for (int connection = 0; connection < 2; ++connection) {
    client.setPath("");
    client.setPath(argv[1]);
    for (int index = 0; index < 3; ++index) {
      const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(8);
      while (!client.frame().has_value() && std::chrono::steady_clock::now() < deadline) {
        client.tick(std::chrono::steady_clock::now());
        pollfd descriptor{.fd = client.fd(), .events = POLLIN};
        if (::poll(&descriptor, 1, 20) > 0) client.dispatch(descriptor.revents);
      }
      const auto frame = client.frame();
      if (!frame.has_value()) return 3;
      const std::size_t size = static_cast<std::size_t>(frame->width) * frame->height * 4;
      const std::vector<unsigned char> pixels(frame->pixels, frame->pixels + size);
      std::this_thread::sleep_for(std::chrono::milliseconds(75));
      if (std::memcmp(pixels.data(), frame->pixels, size) != 0) return 4;
      client.releaseFrame();
    }
  }
  std::cout << "Upstream reader: frames, stable shared buffers and reconnect OK\n";
}
