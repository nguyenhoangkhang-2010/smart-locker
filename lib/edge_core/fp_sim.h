// Phân tích lệnh của "cảm biến vân tay ảo" (demo Wokwi, không cần phần cứng): gõ vào Serial Monitor
//   fp 3   -> vân tay của ô mẫu 3 (nhân viên hợp lệ)      fp x   hoặc   fp -1   -> ngón tay lạ, không khớp
#pragma once
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

namespace edge {

// true nếu line là lệnh hợp lệ; *result = ô mẫu (> 0) hoặc -1 (không khớp). "fp 0" và mọi thứ khác: false.
inline bool parseFingerprintCommand(const char* line, int* result) {
  while (*line == ' ' || *line == '\t') ++line;
  if (tolower((unsigned char)line[0]) != 'f' || tolower((unsigned char)line[1]) != 'p') return false;
  line += 2;
  if (*line != ' ' && *line != '\t') return false;
  while (*line == ' ' || *line == '\t') ++line;
  if (tolower((unsigned char)*line) == 'x') { *result = -1; return true; }
  char* end = nullptr;
  long n = strtol(line, &end, 10);
  if (end == line) return false;
  while (*end == ' ' || *end == '\t' || *end == '\r' || *end == '\n') ++end;
  if (*end != 0) return false;
  if (n == -1) { *result = -1; return true; }
  if (n < 1 || n > 200) return false;
  *result = int(n);
  return true;
}

}  // namespace edge
