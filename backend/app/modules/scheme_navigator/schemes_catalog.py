from typing import List, Dict, Any
from decimal import Decimal

SEED_SCHEMES: List[Dict[str, Any]] = [
    {
        "scheme_code": "PMMY-MUDRA",
        "name": "Pradhan Mantri Mudra Yojana (PMMY)",
        "ministry": "Ministry of Finance",
        "category": "credit_guarantee",
        "description": "Refinance and credit facility up to ₹10 Lakh for micro/small non-corporate, non-farm enterprises across Shishu (up to ₹50k), Kishore (₹50k to ₹5L), and Tarun (₹5L to ₹10L).",
        "benefits_summary": "Collateral-free institutional credit up to ₹10 Lakh with subsidized interest rates and zero processing fees for Shishu category.",
        "max_subsidy_amount": Decimal("1000000.00"),
        "rules": [
            {
                "rule_key": "annual_turnover",
                "operator": "<=",
                "target_value": "50000000.00",
                "rule_description": "Annual turnover must not exceed ₹5 Crore (Micro classification)"
            },
            {
                "rule_key": "industry_sector",
                "operator": "IN",
                "target_value": "Manufacturing, Services, Trading, Retail",
                "rule_description": "Enterprise must operate in non-farm manufacturing, services, or trade"
            }
        ],
        "checklist": {
            "eligibility_summary": "Non-corporate small business segment (NCSBS) comprising proprietorship/partnership firms in non-farm rural or urban areas.",
            "required_documents": [
                "Udyam Registration Certificate or Trade License",
                "Proof of Identity (PAN Card of Proprietor/Partners)",
                "Proof of Address (Utility bill, lease agreement, or municipal certificate)",
                "Last 6 months Bank Account Statement",
                "Quotation of machinery, equipment, or goods to be purchased (for term loans)"
            ],
            "application_steps": [
                "1. Prepare quotation and estimated fund requirements.",
                "2. Visit Udyamimitra portal (udyamimitra.in) or any commercial/Gramin bank branch.",
                "3. Submit Mudra application form along with KYC and bank statements.",
                "4. Bank sanctions loan under Shishu, Kishore, or Tarun tier with Mudra Card."
            ],
            "portal_url": "https://www.udyamimitra.in",
            "official_guidelines_url": "https://www.mudra.org.in"
        }
    },
    {
        "scheme_code": "CGTMSE",
        "name": "Credit Guarantee Fund Trust for Micro and Small Enterprises",
        "ministry": "Ministry of MSME",
        "category": "credit_guarantee",
        "description": "Collateral-free credit facility up to ₹200 Lakh (₹2 Crore) for new and existing Micro and Small Enterprises backed by 75% to 85% credit guarantee cover.",
        "benefits_summary": "Access to bank credit without offering third-party collateral or personal asset guarantees. Guarantee fee subsidized for women and micro units.",
        "max_subsidy_amount": Decimal("20000000.00"),
        "rules": [
            {
                "rule_key": "turnover_bracket",
                "operator": "IN",
                "target_value": "Micro (< 5 Cr), Small (5-50 Cr)",
                "rule_description": "Enterprise must be classified as Micro or Small under the MSMED Act"
            },
            {
                "rule_key": "industry_sector",
                "operator": "IN",
                "target_value": "Manufacturing, Services",
                "rule_description": "Eligible for manufacturing and service sectors (retail trade covered up to ₹1 Cr)"
            },
            {
                "rule_key": "has_udyam",
                "operator": "=",
                "target_value": "true",
                "rule_description": "Valid Udyam Registration Certificate is mandatory"
            }
        ],
        "checklist": {
            "eligibility_summary": "All new and existing Micro and Small enterprises engaged in manufacturing or service activities.",
            "required_documents": [
                "Udyam Registration Certificate",
                "Audited/CA-certified Balance Sheets and P&L for past 2 years (for existing units)",
                "Detailed Project Report (DPR) with CMA data",
                "GST returns (GSTR-3B) for the preceding 12 months",
                "Proof of enterprise promoter KYC (PAN, Aadhaar)"
            ],
            "application_steps": [
                "1. Prepare Detailed Project Report (DPR) detailing capital expenditure and working capital requirements.",
                "2. Apply to any scheduled commercial bank or eligible financial institution.",
                "3. Bank appraises loan and requests guarantee cover directly from CGTMSE trust portal.",
                "4. Collateral-free sanction letter issued upon CGTMSE approval."
            ],
            "portal_url": "https://www.cgtmse.in",
            "official_guidelines_url": "https://www.cgtmse.in/About_Us.aspx"
        }
    },
    {
        "scheme_code": "PMEGP",
        "name": "Prime Minister's Employment Generation Programme",
        "ministry": "Ministry of MSME",
        "category": "subsidy",
        "description": "Credit-linked subsidy programme to generate self-employment. Provides 15% to 35% margin money subsidy on project cost up to ₹50 Lakh for manufacturing and ₹20 Lakh for services.",
        "benefits_summary": "Direct government grant (margin money) up to 35% of project cost deposited in beneficiary escrow account with 3-year lock-in.",
        "max_subsidy_amount": Decimal("1750000.00"),
        "rules": [
            {
                "rule_key": "business_type",
                "operator": "IN",
                "target_value": "Proprietorship, Partnership, Self Help Group",
                "rule_description": "Eligible for individual proprietors, partnership firms, and SHGs"
            },
            {
                "rule_key": "industry_sector",
                "operator": "IN",
                "target_value": "Manufacturing, Services",
                "rule_description": "Applicable for new manufacturing or service greenfield projects"
            },
            {
                "rule_key": "vintage_years",
                "operator": "<=",
                "target_value": "3",
                "rule_description": "Prioritized for new or recently established business ventures"
            }
        ],
        "checklist": {
            "eligibility_summary": "Any individual above 18 years of age. For projects above ₹10 Lakh in manufacturing or ₹5 Lakh in services, minimum 8th class pass.",
            "required_documents": [
                "Detailed Project Report (DPR) with cost breakdown",
                "Educational qualification certificate (minimum 8th class pass for projects > ₹10L)",
                "Caste / Special Category certificate (if claiming 25-35% subsidy)",
                "Rural area certificate from Gram Panchayat (if seeking rural subsidy rates)",
                "EDP (Entrepreneurship Development Programme) training certificate"
            ],
            "application_steps": [
                "1. Register online on the KVIC PMEGP portal (kviconline.gov.in).",
                "2. Upload DPR, KYC, and qualification certificates.",
                "3. District Task Force Committee (DLTFC) reviews and forwards to financing bank.",
                "4. Bank sanctions loan and claims margin money subsidy from KVIC nodal bank."
            ],
            "portal_url": "https://www.kviconline.gov.in/pmegpeportal",
            "official_guidelines_url": "https://www.msme.gov.in/pmegp"
        }
    },
    {
        "scheme_code": "PM-SVANIDHI",
        "name": "PM Street Vendor's AtmaNirbhar Nidhi",
        "ministry": "Ministry of Housing and Urban Affairs",
        "category": "subsidy",
        "description": "Special micro-credit facility providing affordable working capital loans of ₹10,000 (1st tranche), ₹20,000 (2nd tranche), and ₹50,000 (3rd tranche) with 7% interest subsidy.",
        "benefits_summary": "Collateral-free working capital loan, 7% annual interest subsidy credited quarterly, and cash-back rewards up to ₹1,200/year on digital transactions.",
        "max_subsidy_amount": Decimal("50000.00"),
        "rules": [
            {
                "rule_key": "annual_turnover",
                "operator": "<=",
                "target_value": "1500000.00",
                "rule_description": "Applicable for micro enterprises with annual revenue under ₹15 Lakh"
            },
            {
                "rule_key": "industry_sector",
                "operator": "IN",
                "target_value": "Retail, Services, Trading",
                "rule_description": "Vending or micro trade in urban or peri-urban localities"
            }
        ],
        "checklist": {
            "eligibility_summary": "Urban street vendors, hawkers, and micro-retailers in possession of Vending Certificate or Letter of Recommendation.",
            "required_documents": [
                "Vending Certificate / Identity Card issued by Urban Local Body (ULB)",
                "Aadhaar Card linked with mobile number",
                "Bank Account Passbook / Statement",
                "UPI QR code handle for digital transaction incentives"
            ],
            "application_steps": [
                "1. Check vendor survey status on pmsvanidhi.mohua.gov.in.",
                "2. Fill online application with Aadhaar authentication.",
                "3. Select lending institution (Public/Private Bank, Co-op Bank, NBFC).",
                "4. Receive ₹10k/₹20k/₹50k direct disbursement to bank account."
            ],
            "portal_url": "https://pmsvanidhi.mohua.gov.in",
            "official_guidelines_url": "https://mohua.gov.in/cms/PMSvanidhi.php"
        }
    },
    {
        "scheme_code": "STAND-UP-INDIA",
        "name": "Stand-Up India Scheme for Greenfield Ventures",
        "ministry": "Ministry of Finance",
        "category": "credit_guarantee",
        "description": "Bank loans between ₹10 Lakh and ₹1 Crore to at least one Scheduled Caste (SC) or Scheduled Tribe (ST) borrower and at least one woman borrower per bank branch.",
        "benefits_summary": "Composite loan covering term loan and working capital up to 85% of project cost with margin money requirements as low as 15%.",
        "max_subsidy_amount": Decimal("10000000.00"),
        "rules": [
            {
                "rule_key": "social_category",
                "operator": "IN",
                "target_value": "SC, ST, Women",
                "rule_description": "Borrower must be SC, ST, or a Woman entrepreneur (or hold >= 51% stake)"
            },
            {
                "rule_key": "vintage_years",
                "operator": "<=",
                "target_value": "2",
                "rule_description": "Applicable exclusively to Greenfield enterprises (first-time venture in sector)"
            },
            {
                "rule_key": "industry_sector",
                "operator": "IN",
                "target_value": "Manufacturing, Services, Trading",
                "rule_description": "Eligible in manufacturing, services, agri-allied, or trading sectors"
            }
        ],
        "checklist": {
            "eligibility_summary": "SC/ST and/or woman entrepreneurs above 18 years of age. In case of non-individual enterprises, at least 51% shareholding held by SC/ST or woman.",
            "required_documents": [
                "Proof of SC/ST category (if applicable) or proof of woman entrepreneurship",
                "Udyam Registration Certificate",
                "Project Report including balance sheet projections and fund utilization",
                "Pollution clearance certificates (for manufacturing)",
                "Promoter contribution proof (15% margin money)"
            ],
            "application_steps": [
                "1. Access standupmitra.in portal and register profile.",
                "2. Opt for handholding support (trainee borrower) or apply directly as ready borrower.",
                "3. Submit application to target bank branch or lead district manager.",
                "4. Bank sanctions composite loan between ₹10L and ₹100L."
            ],
            "portal_url": "https://www.standupmitra.in",
            "official_guidelines_url": "https://www.standupmitra.in/Home/SUISchemes"
        }
    },
    {
        "scheme_code": "MSME-ZED",
        "name": "MSME Sustainable (ZED) Certification Scheme",
        "ministry": "Ministry of MSME",
        "category": "subsidy",
        "description": "Provides financial support up to 80% subsidy on certification costs across Bronze, Silver, and Gold levels to promote Zero Defect, Zero Effect manufacturing.",
        "benefits_summary": "Up to 80% subsidy for Micro (60% Small, 50% Medium) on ZED assessment fee, plus ₹5 Lakh financial assistance for technology upgradation and green testing.",
        "max_subsidy_amount": Decimal("500000.00"),
        "rules": [
            {
                "rule_key": "industry_sector",
                "operator": "IN",
                "target_value": "Manufacturing",
                "rule_description": "Must be an active manufacturing unit"
            },
            {
                "rule_key": "has_udyam",
                "operator": "=",
                "target_value": "true",
                "rule_description": "Must possess a valid Udyam Registration"
            },
            {
                "rule_key": "turnover_bracket",
                "operator": "IN",
                "target_value": "Micro (< 5 Cr), Small (5-50 Cr), Medium (50-250 Cr)",
                "rule_description": "Must qualify as Micro, Small, or Medium manufacturing enterprise"
            }
        ],
        "checklist": {
            "eligibility_summary": "All manufacturing MSMEs with valid Udyam registration committed to zero defect manufacturing and environmental sustainability.",
            "required_documents": [
                "Udyam Registration Certificate",
                "Factory electricity bills / industrial license",
                "Process flowcharts and quality assurance documentation",
                "Self-assessment declaration against ZED Bronze/Silver/Gold parameters",
                "Bank account verification for direct subsidy reimbursement"
            ],
            "application_steps": [
                "1. Register on zed.msme.gov.in using Udyam number.",
                "2. Take free ZED Pledge and access self-assessment module.",
                "3. Complete desk verification and third-party on-site assessment.",
                "4. Receive ZED certificate and automatic government subsidy reimbursement."
            ],
            "portal_url": "https://zed.msme.gov.in",
            "official_guidelines_url": "https://zed.msme.gov.in/guidelines"
        }
    }
]
