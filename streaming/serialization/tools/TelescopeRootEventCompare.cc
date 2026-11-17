#include "TelescopeRootEventComparison.h"

#include <exception>
#include <iostream>

int main(int argc, char **argv) {
  if (argc != 3) {
    std::cerr
        << "使用法: TelescopeRootEventCompare EXPECTED_ROOT ACTUAL_ROOT\n";
    return 64;
  }

  try {
    const auto summary =
        eic::telescope::streaming::requireEquivalentRootEventFiles(argv[1],
                                                                   argv[2]);
    std::cout << "PODIO ROOT全eventの厳密比較に成功しました: events="
              << summary.entries << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "PODIO ROOT全eventの厳密比較に失敗しました: " << error.what()
              << '\n';
    return 1;
  }
}
