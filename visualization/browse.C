#include <TBrowser.h>
#include <TFile.h>
#include <TError.h>

// ROOT GUI browser: root --web=off -l 'browse.C("result/regression_001/step7/alignment.root")'
void browse(const char* filename = "result/regression_001/step7/alignment.root") {
  TFile *f = TFile::Open(filename, "READ");
  if (!f || f->IsZombie()) {
    ::Error("browse_alignment", "Cannot open %s", filename);
    return;
  }
  new TBrowser("alignment_browser", f, "EIC alignment ROOT plots");
}
