// Display 18 first-linearized RMS errors and formal sigmas from an offline scan.
// This plot does not run Pede, change Geometry or establish uncertainty coverage.
// Use explicit input and a NEW output directory; existing files are preserved.
#include <TFile.h>
#include <TCanvas.h>
#include <TGraphErrors.h>
#include <TMultiGraph.h>
#include <TLegend.h>
#include <TAxis.h>
#include <TPad.h>
#include <TSystem.h>
#include <TString.h>
#include <TStyle.h>
#include <TNamed.h>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

void plot_all_18d_scan(const char *input, const char *output) {
  if (!input || !*input || !output || !*output)
    throw std::invalid_argument("Explicit ROOT input and new output directory required");
  std::unique_ptr<TFile> in(TFile::Open(input, "READ"));
  if (!in || in->IsZombie())
    throw std::runtime_error("Cannot read offline linearized ROOT input");
  const char *scope = "OFFLINE_FIRST_ITERATION_LINEARIZED_18D_NOT_PEDE_OR_GEOMETRY_CORRECTION";
  auto *kind = dynamic_cast<TNamed*>(in->Get("analysis_scope"));
  if (!kind || std::string(kind->GetTitle()) != scope)
    throw std::runtime_error("Input is not a scoped offline first-linearized 18D diagnostic");
  auto *truth = dynamic_cast<TNamed*>(in->Get("truth_available"));
  if (!truth || std::string(truth->GetTitle()) != "true")
    throw std::runtime_error("RMS accuracy plots require independently verified Truth input");
  auto *identity = dynamic_cast<TNamed*>(in->Get("input_sha256"));
  if (!identity || std::string(identity->GetTitle()).size() != 64)
    throw std::runtime_error("Missing source native-equation SHA256 metadata");
  const std::string inputSha = identity->GetTitle();
  if (!gSystem->AccessPathName(output))
    throw std::runtime_error("Output already exists; choose another directory");
  const char *axes[] = {"X", "Y", "Z", "Rx", "Ry", "Rz"};
  struct Curves { TGraphErrors *rms; TGraphErrors *sigma; };
  std::vector<Curves> curves;
  double xmin = std::numeric_limits<double>::infinity(), xmax = 0;
  for (int layer = 1; layer <= 3; ++layer) {
    for (const char *axis : axes) {
      const TString key = Form("L%d_%s", layer, axis);
      auto *rms = dynamic_cast<TGraphErrors*>(in->Get(Form("rms_error_%s", key.Data())));
      if (!rms || rms->GetN() == 0)
        throw std::runtime_error("Missing or empty RMS graph; input needs verified Truth");
      auto *a = static_cast<TGraphErrors*>(rms->Clone(Form("rms_clone_%s", key.Data())));
      auto *sigma = dynamic_cast<TGraphErrors*>(in->Get(Form("formal_sigma_%s", key.Data())));
      if (!sigma || sigma->GetN() != a->GetN())
        throw std::runtime_error("Missing/mismatched formal-sigma graph");
      auto *b = static_cast<TGraphErrors*>(sigma->Clone(Form("sig_clone_%s", key.Data())));
      for (int j = 0; j < a->GetN(); ++j) {
        double x, y, sx, sy;
        a->GetPoint(j, x, y); b->GetPoint(j, sx, sy);
        if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(sx) ||
            !std::isfinite(sy) || x <= 0 || y < 0 || sy <= 0 || x != sx)
          throw std::runtime_error("Invalid RMS/formal-sigma point");
        xmin = std::min(xmin, x); xmax = std::max(xmax, x);
      }
      curves.push_back({a, b});
    }
  }
  in->Close();
  if (gSystem->mkdir(output, true) != 0)
    throw std::runtime_error("Cannot create fresh plot output directory");
  std::unique_ptr<TFile> saved(TFile::Open(Form("%s/linearized18_rms.root", output), "CREATE"));
  if (!saved || saved->IsZombie())
    throw std::runtime_error("Cannot create ROOT plot output; partial directory retained");
  TNamed("analysis_scope", scope).Write();
  TNamed("truth_available", "true").Write();
  TNamed("source_root_filename", input).Write();
  TNamed("source_native_input_sha256", inputSha.c_str()).Write();
  gStyle->SetOptStat(0);
  for (int layer = 1; layer <= 3; ++layer) {
    auto *c = new TCanvas(Form("c_L%d_18d", layer), Form("L%d: first-linearized RMS and formal sigma", layer), 1400, 850);
    c->Divide(3, 2);
    for (int i = 0; i < 6; ++i) {
      auto *a = curves.at(6*(layer-1)+i).rms;
      auto *b = curves.at(6*(layer-1)+i).sigma;
      saved->cd();
      a->SetName(Form("rms_error_L%d_%s", layer, axes[i]));
      b->SetName(Form("formal_sigma_L%d_%s", layer, axes[i]));
      a->Write(); b->Write();
      c->cd(i+1);
      gPad->SetLeftMargin(0.14); gPad->SetBottomMargin(0.14);
      gPad->SetGrid(); gPad->SetLogx();
      a->SetLineColor(kBlue+1); a->SetMarkerColor(kBlue+1); a->SetMarkerStyle(20); a->SetLineWidth(2);
      b->SetLineColor(kRed+1); b->SetMarkerColor(kRed+1); b->SetMarkerStyle(24); b->SetLineWidth(2);
      auto *m = new TMultiGraph();
      m->SetTitle(Form("L%d %s;Tracks per cohort;Linearized error (%s)", layer, axes[i], i < 3 ? "#mum" : "mrad"));
      m->Add(a, "LP"); m->Add(b, "LP"); m->Draw("A"); m->SetMinimum(0);
      m->GetXaxis()->SetLimits(.9*xmin, 1.25*xmax);
      auto *leg = new TLegend(0.43,0.73,0.89,0.89);
      leg->SetBorderSize(0); leg->SetFillStyle(0);
      leg->AddEntry(a,"Error RMS (singleton: |error|)","lp");
      leg->AddEntry(b,"Formal sigma; coverage unvalidated","lp"); leg->Draw();
    }
    saved->cd();
    c->Write();
    for (const char *extension : {"png", "pdf"}) {
      const TString filename = Form("%s/L%d_6d_accuracy.%s", output, layer, extension);
      c->SaveAs(filename);
      std::ifstream image(filename.Data(), std::ios::binary | std::ios::ate);
      if (!image || image.tellg() <= 0)
        throw std::runtime_error("Failed to save image; partial outputs retained");
    }
    delete c;
  }
  saved->Close();
}
