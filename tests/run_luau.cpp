// Optional harness: link against the Luau library from a Noctalia build.
#include <Luau/Compiler.h>
#include <lua.h>
#include <lualib.h>

#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

int main(int argc, char** argv) {
  const bool checkOnly = argc > 1 && std::string(argv[1]) == "--check";
  lua_State* state = luaL_newstate();
  luaL_openlibs(state);
  for (int index = checkOnly ? 2 : 1; index < argc; ++index) {
    std::ifstream input(argv[index]);
    if (!input) {
      std::cerr << "Cannot read " << argv[index] << '\n';
      lua_close(state);
      return 1;
    }
    const std::string source{std::istreambuf_iterator<char>(input), {}};
    const auto bytecode = Luau::compile(source);
    if (luau_load(state, argv[index], bytecode.data(), bytecode.size(), 0)
        || (!checkOnly && lua_pcall(state, 0, 0, 0))) {
      std::cerr << argv[index] << ": " << lua_tostring(state, -1) << '\n';
      lua_close(state);
      return 1;
    }
    if (checkOnly) lua_pop(state, 1);
  }
  lua_close(state);
}
